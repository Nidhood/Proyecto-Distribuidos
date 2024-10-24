import grpc
import time
import logging
from datetime import datetime
import taxi_service_pb2
import taxi_service_pb2_grpc


def check_server_health(address):
    try:
        with grpc.insecure_channel(address) as channel:
            stub = taxi_service_pb2_grpc.TaxiDatabaseServiceStub(channel)
            response = stub.HealthCheck(taxi_service_pb2.HealthCheckRequest())
            return response.status, response.message
    except grpc.RpcError as e:
        return False, str(e)



class HealthChecker:
    def __init__(self, main_address='localhost:50051', backup_address='localhost:50052'):
        self.main_address = main_address
        self.backup_address = backup_address
        self.active_server = main_address
        self.logger = logging.getLogger(__name__)

    def switch_server(self):
        if self.active_server == self.main_address:
            self.active_server = self.backup_address
            self.logger.info("Switching to backup server")
        else:
            self.active_server = self.main_address
            self.logger.info("Switching back to main server")

    def run(self):
        consecutive_failures = 0
        while True:
            timestamp = datetime.now()
            main_status, main_message = check_server_health(self.main_address)
            backup_status, backup_message = check_server_health(self.backup_address)

            self.logger.info(f"Health Check Results ({timestamp}):")
            self.logger.info(f"Main Server: {'OK' if main_status else 'FAILED'} - {main_message}")
            self.logger.info(f"Backup Server: {'OK' if backup_status else 'FAILED'} - {backup_message}")

            # Lógica de failover
            if not main_status and self.active_server == self.main_address:
                consecutive_failures += 1
                if consecutive_failures >= 3:  # Cambiar después de 3 fallos consecutivos
                    self.switch_server()
                    consecutive_failures = 0
            elif main_status and self.active_server == self.backup_address:
                self.switch_server()  # Volver al servidor principal si se recupera
                consecutive_failures = 0
            elif main_status:
                consecutive_failures = 0

            time.sleep(5)  # Verificar cada 5 segundos


if __name__ == '__main__':
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    checker = HealthChecker()
    checker.run()
