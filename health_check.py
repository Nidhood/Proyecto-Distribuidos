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
    def __init__(self, server_address='localhost:50051',backup_address='localhost:50054', broker_address='localhost:50053', check_interval=10):
        self.monitor_thread = None
        self.server_address = server_address
        self.broker_address = broker_address
        self.backup_address = backup_address
        self.check_interval = check_interval
        self.stop_event = Event()
        self.consecutive_failures = 0
        self.MAX_FAILURES = 3
        self.is_server_healthy = True

        self.logger = logging.getLogger(__name__)
        self.logger.setLevel(logging.INFO)

    def check_server_health(self):
        """Realiza una verificación de salud del servidor usando gRPC"""
        try:
            with grpc.insecure_channel(self.server_address) as channel:
                stub = taxi_service_pb2_grpc.TaxiDatabaseServiceStub(channel)
                response = stub.HealthCheck(
                    taxi_service_pb2.HealthCheckRequest(),
                    timeout=5
                )
                return response.status, response.message
        except grpc.RpcError as e:
            status = e.code().name
            return False, f"Error gRPC: {status} - {e.details()}"
        except Exception as e:
            return False, f"Error inesperado: {str(e)}"

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
                self.logger.info("Servidor recuperado después de fallos")
            self.consecutive_failures = 0
            self.is_server_healthy = True
        else:
            self.consecutive_failures += 1
            self.logger.warning(
                f"Fallo de salud #{self.consecutive_failures}: {message}"
            )

            if self.consecutive_failures >= self.MAX_FAILURES:
                self.is_server_healthy = False
                self.logger.error(
                    f"Servidor considerado caído después de {self.MAX_FAILURES} fallos"
                )

    def monitor_server(self):
        """Monitorea continuamente la salud del servidor y del broker"""
        while not self.stop_event.is_set():
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            self.logger.info(f"Verificando salud del servidor en {timestamp}")

            server_healthy, server_message = self.check_server_health()
            broker_healthy, broker_message = self.check_broker_health()

            if server_healthy and broker_healthy:
                self.handle_health_status(True, "Both server and broker are healthy")
            elif not server_healthy:
                self.handle_health_status(False, f"Server health check failed: {server_message}")
                self.promote_backup_to_primary()
                self.server_address = self.backup_address

            else:
                self.handle_health_status(False, f"Broker health check failed: {broker_message}")

            self.stop_event.wait(timeout=self.check_interval)

    def promote_backup_to_primary(self):
        self.logger.info("Promoviendo servidor de respaldo a primario")
        try:
            with grpc.insecure_channel(self.backup_address) as channel:
                stub = taxi_service_pb2_grpc.TaxiDatabaseServiceStub(channel)
                request = taxi_service_pb2.PromoteToPrimaryRequest()
                response = stub.PromoteToPrimary(request)
                if response.success:
                    self.logger.info("Servidor de respaldo promovido a primario exitosamente")
                else:
                    self.logger.error("Fallo al promover el servidor de respaldo a primario")
        except Exception as e:
            self.logger.error(f"Error promoviendo el servidor de respaldo a primario: {e}")

    def start(self):
        """Inicia el monitoreo de salud en un thread separado"""
        self.monitor_thread = Thread(target=self.monitor_server)
        self.monitor_thread.daemon = True
        self.monitor_thread.start()
        self.logger.info(f"Monitoreo de salud iniciado para {self.server_address} y {self.broker_address}")

    def stop(self):
        """Detiene el monitoreo de salud"""
        self.logger.info("Deteniendo monitoreo de salud...")
        self.stop_event.set()
        self.monitor_thread.join(timeout=5)
        self.logger.info("Monitoreo de salud detenido")

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