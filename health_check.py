import grpc
import time
import logging
from datetime import datetime
import taxi_service_pb2
import taxi_service_pb2_grpc
from threading import Event, Thread


class ServerHealthCheck:
    def __init__(self, server_address='localhost:50052', check_interval=10):
        self.monitor_thread = None
        self.server_address = server_address
        self.check_interval = check_interval
        self.stop_event = Event()
        self.consecutive_failures = 0
        self.MAX_FAILURES = 3
        self.is_server_healthy = True

        # Configuración del logger
        self.logger = logging.getLogger(__name__)
        self.logger.setLevel(logging.INFO)

    def check_server_health(self):
        """Realiza una verificación de salud del servidor usando gRPC"""
        try:
            with grpc.insecure_channel(self.server_address) as channel:
                stub = taxi_service_pb2_grpc.TaxiDatabaseServiceStub(channel)
                response = stub.HealthCheck(
                    taxi_service_pb2.HealthCheckRequest(),
                    timeout=5  # 5 segundos de timeout
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
        """Monitorea continuamente la salud del servidor"""
        while not self.stop_event.is_set():
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            self.logger.info(f"Verificando salud del servidor en {timestamp}")

            is_healthy, message = self.check_server_health()
            self.handle_health_status(is_healthy, message)

            # Esperar el intervalo especificado o hasta que se active stop_event
            self.stop_event.wait(timeout=self.check_interval)

    def start(self):
        """Inicia el monitoreo de salud en un thread separado"""
        self.monitor_thread = Thread(target=self.monitor_server)
        self.monitor_thread.daemon = True
        self.monitor_thread.start()
        self.logger.info(f"Monitoreo de salud iniciado para {self.server_address}")

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
        # Mantener el programa principal ejecutándose
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        health_checker.stop()


if __name__ == '__main__':
    main()
