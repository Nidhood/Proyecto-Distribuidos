import grpc
import time
import logging
from datetime import datetime
from threading import Event, Thread

import taxi_service_pb2
import taxi_service_pb2_grpc
import broker_service_pb2
import broker_service_pb2_grpc


class ServerHealthCheck:
    def __init__(self, server_address='localhost:50051', backup_address='localhost:50054',
                 broker_address='localhost:50053', broker_backup_address='localhost:50055', check_interval=10):
        self.monitor_thread = None
        self.server_address = server_address
        self.broker_address = broker_address
        self.backup_address = backup_address
        self.broker_backup_address = broker_backup_address

        self.check_interval = check_interval
        self.stop_event = Event()
        self.consecutive_failures = 0
        self.MAX_FAILURES = 3
        self.is_server_healthy = True

        # Configuración mejorada del logger
        self.logger = logging.getLogger('HealthCheck')
        if not self.logger.handlers:
            self.logger.setLevel(logging.INFO)
            formatter = logging.Formatter('🏥 %(asctime)s - %(message)s')
            ch = logging.StreamHandler()
            ch.setFormatter(formatter)
            self.logger.addHandler(ch)

        self.primary_is_active = True

    def check_server_health(self):
        """Realiza una verificación de salud del servidor usando gRPC"""
        address = self.server_address if self.primary_is_active else self.backup_address
        try:
            with grpc.insecure_channel(address) as channel:
                stub = taxi_service_pb2_grpc.TaxiDatabaseServiceStub(channel)
                response = stub.HealthCheck(
                    taxi_service_pb2.HealthCheckRequest(),
                    timeout=5
                )
                return response.status, response.message
        except Exception as e:
            return False, f"Error inesperado: {str(e)}"

    def switch_servers(self):
        """Cambia entre servidor primario y secundario"""
        if self.primary_is_active:
            self.logger.info("🔄 Iniciando cambio al servidor de respaldo...")
            self.promote_backup_to_primary()
            try:
                with grpc.insecure_channel(self.server_address) as channel:
                    stub = taxi_service_pb2_grpc.TaxiDatabaseServiceStub(channel)
                    stub.DemoteToSecondary(taxi_service_pb2.PromoteToPrimaryRequest())
                    self.logger.info("⬇️ Servidor primario degradado exitosamente")
            except:
                self.logger.warning("⚠️ No se pudo contactar al servidor primario para degradarlo")
        else:
            self.logger.info("🔄 Intentando recuperar servidor primario...")
            try:
                with grpc.insecure_channel(self.server_address) as channel:
                    stub = taxi_service_pb2_grpc.TaxiDatabaseServiceStub(channel)
                    response = stub.HealthCheck(taxi_service_pb2.HealthCheckRequest())
                    if response.status:
                        with grpc.insecure_channel(self.backup_address) as channel:
                            backup_stub = taxi_service_pb2_grpc.TaxiDatabaseServiceStub(channel)
                            backup_stub.DemoteToSecondary(taxi_service_pb2.PromoteToPrimaryRequest())
                        self.primary_is_active = True
                        self.consecutive_failures = 0
                        self.logger.info("✅ Servidor primario recuperado y activado")
            except:
                self.logger.error("❌ No se pudo recuperar el servidor primario")

    def check_broker_health(self):
        """Realiza una verificación de salud del broker usando gRPC"""
        try:
            with grpc.insecure_channel(self.broker_address) as channel:
                stub = broker_service_pb2_grpc.BrokerServiceStub(channel)
                response = stub.HealthCheck(
                    broker_service_pb2.HealthCheckRequest(),
                    timeout=5
                )
                return response.status, response.message
        except grpc.RpcError as e:
            status = e.code().name
            return False, f"Error gRPC: {status} - {e.details()}"
        except Exception as e:
            return False, f"Error inesperado: {str(e)}"

    def handle_health_status(self, is_healthy, message):
        """Maneja el estado de salud del servidor"""
        if is_healthy:
            if self.consecutive_failures > 0:
                self.logger.info("💚 Servidor recuperado después de fallos")
            self.consecutive_failures = 0
            self.is_server_healthy = True
        else:
            self.consecutive_failures += 1
            self.logger.warning(f"🔴 Fallo de salud #{self.consecutive_failures}: {message}")

            if self.consecutive_failures >= self.MAX_FAILURES:
                self.is_server_healthy = False
                self.logger.error(f"💔 Servidor considerado caído después de {self.MAX_FAILURES} fallos")
                self.switch_servers()

    def monitor_server(self):
        """Monitorea continuamente la salud del servidor y del broker"""
        while not self.stop_event.is_set():
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            self.logger.info(f"🔍 Verificando salud del sistema en {timestamp}")

            server_healthy, server_message = self.check_server_health()
            broker_healthy, broker_message = self.check_broker_health()

            if server_healthy and broker_healthy:
                self.handle_health_status(True, "✨ Servidor y broker funcionando correctamente")
            elif not server_healthy:
                self.handle_health_status(False, f"🚨 Fallo en servidor: {server_message}")
                self.promote_backup_to_primary()
                self.server_address = self.backup_address
            else:
                self.handle_health_status(False, f"🚨 Fallo en broker: {broker_message}")
                self.promote_broker_backup_to_primary()
                self.broker_address = self.broker_backup_address

            self.stop_event.wait(timeout=self.check_interval)

    def promote_backup_to_primary(self):
        self.logger.info("⬆️ Promoviendo servidor de respaldo a primario")
        try:
            with grpc.insecure_channel(self.backup_address) as channel:
                stub = taxi_service_pb2_grpc.TaxiDatabaseServiceStub(channel)
                request = taxi_service_pb2.PromoteToPrimaryRequest()
                response = stub.PromoteToPrimary(request)
                if response.success:
                    self.logger.info("✅ Servidor de respaldo promovido exitosamente")
                else:
                    self.logger.error("❌ Fallo al promover el servidor de respaldo")
        except Exception as e:
            self.logger.error(f"💥 Error en promoción del servidor: {e}")

    def promote_broker_backup_to_primary(self):
        self.logger.info("⬆️ Promoviendo broker de respaldo a primario")
        try:
            with grpc.insecure_channel(self.broker_backup_address) as channel:
                stub = broker_service_pb2_grpc.BrokerServiceStub(channel)
                request = broker_service_pb2.PromoteToPrimaryRequest()
                response = stub.PromoteToPrimary(request)
                if response.success:
                    self.logger.info("✅ Broker de respaldo promovido exitosamente")
                else:
                    self.logger.error("❌ Fallo al promover el broker de respaldo")
        except Exception as e:
            self.logger.error(f"💥 Error en promoción del broker: {e}")

    def start(self):
        """Inicia el monitoreo de salud en un thread separado"""
        self.monitor_thread = Thread(target=self.monitor_server)
        self.monitor_thread.daemon = True
        self.monitor_thread.start()
        self.logger.info(f"🚀 Monitoreo iniciado para servidor: {self.server_address} y broker: {self.broker_address}")

    def stop(self):
        """Detiene el monitoreo de salud"""
        self.logger.info("🛑 Deteniendo monitoreo de salud...")
        self.stop_event.set()
        self.monitor_thread.join(timeout=5)
        self.logger.info("✅ Monitoreo de salud detenido correctamente")


def main():
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )

    health_checker = ServerHealthCheck(check_interval=10)
    try:
        health_checker.start()
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        health_checker.stop()


if __name__ == '__main__':
    main()
