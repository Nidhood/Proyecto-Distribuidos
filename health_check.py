import logging
import zmq
import time
from threading import Thread
import grpc
import taxi_service_pb2
import taxi_service_pb2_grpc
import json

class HealthCheckService:
    def __init__(self, broker_pub_port=5557, broker_sub_port=5558, server_address='localhost:50051'):
        self.context = zmq.Context()
        self.broker_pub_port = broker_pub_port
        self.broker_sub_port = broker_sub_port
        self.server_address = server_address
        self.active = True

        # Para verificar el broker
        self.test_publisher = self.context.socket(zmq.PUB)
        self.test_subscriber = self.context.socket(zmq.SUB)

        # Para verificar el servidor principal
        self.server_channel = grpc.insecure_channel(server_address)
        self.server_stub = taxi_service_pb2_grpc.TaxiDatabaseServiceStub(self.server_channel)

        self.broker_status = False
        self.server_status = False

        # Configuración de logging
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(levelname)s - %(message)s'
        )

    def check_broker(self):
        """Verifica la salud del broker intentando publicar y recibir mensajes"""
        try:
            # Conectar sockets de prueba
            self.test_publisher.connect(f"tcp://localhost:{self.broker_pub_port}")
            self.test_subscriber.connect(f"tcp://localhost:{self.broker_sub_port}")
            self.test_subscriber.setsockopt_string(zmq.SUBSCRIBE, "health_check")

            # Pequeña pausa para establecer conexiones
            time.sleep(0.5)

            # Enviar mensaje de prueba
            test_message = {
                "tipo": "health_check",
                "timestamp": time.time()
            }
            self.test_publisher.send_string(f"health_check {json.dumps(test_message)}")

            # Configurar poller para timeout
            poller = zmq.Poller()
            poller.register(self.test_subscriber, zmq.POLLIN)

            # Esperar respuesta con timeout
            socks = dict(poller.poll(1000))  # 1 segundo de timeout

            if self.test_subscriber in socks:
                message = self.test_subscriber.recv_string()
                # Verificar que el mensaje sea JSON válido
                try:
                    topic, payload = message.split(" ", 1)
                    response = json.loads(payload)
                    self.broker_status = True
                except:
                    self.broker_status = False
            else:
                self.broker_status = False
                logging.warning("No se recibió respuesta del broker en el tiempo esperado")

        except zmq.ZMQError as e:
            logging.error(f"Error en la comunicación con el broker: {e}")
            self.broker_status = False
        except Exception as e:
            logging.error(f"Error inesperado al verificar broker: {e}")
            self.broker_status = False

    def check_server(self):
        """Verifica la salud del servidor principal usando mensajes ZMQ"""
        try:
            # Suscribirse al topic de respuesta del servidor
            self.test_subscriber.setsockopt_string(zmq.SUBSCRIBE, "health_check")

            # Enviar un mensaje de prueba al servidor a través del broker
            test_message = {
                "tipo": "server_health_check",
                "timestamp": time.time()
            }
            self.test_publisher.send_string(f"server_health_check {json.dumps(test_message)}")

            # Esperar respuesta
            poller = zmq.Poller()
            poller.register(self.test_subscriber, zmq.POLLIN)

            socks = dict(poller.poll(1000))  # 1 segundo de timeout

            if self.test_subscriber in socks:
                message = self.test_subscriber.recv_string()
                try:
                    topic, payload = message.split(" ", 1)
                    response = json.loads(payload)
                    if response.get("tipo") == "health_check_response":
                        self.server_status = True
                    else:
                        self.server_status = False
                except:
                    self.server_status = False
            else:
                self.server_status = False
                logging.warning("No se recibió respuesta del servidor principal")

        except Exception as e:
            logging.error(f"Error al verificar servidor principal: {e}")
            self.server_status = False

    def run_health_checks(self):
        """Ejecuta las verificaciones de salud periódicamente"""
        while self.active:
            self.check_broker()
            self.check_server()

            status_message = (
                "Estado del sistema:\n"
                f"- Broker: {'ACTIVO' if self.broker_status else 'INACTIVO'}\n"
                f"- Servidor Principal: {'ACTIVO' if self.server_status else 'INACTIVO'}"
            )
            logging.info(status_message)

            # Esperar antes de la siguiente verificación
            time.sleep(5)

    def start(self):
        """Inicia el servicio de monitoreo de salud"""
        try:
            self.health_check_thread = Thread(target=self.run_health_checks)
            self.health_check_thread.daemon = True
            self.health_check_thread.start()
            logging.info("Servicio de monitoreo de salud iniciado")
        except Exception as e:
            logging.error(f"Error al iniciar el servicio de monitoreo: {e}")

    def stop(self):
        """Detiene el servicio de monitoreo de salud"""
        try:
            self.active = False
            if hasattr(self, 'health_check_thread'):
                self.health_check_thread.join(timeout=2)

            # Limpiar recursos
            self.test_publisher.close()
            self.test_subscriber.close()
            self.context.term()
            self.server_channel.close()
            logging.info("Servicio de monitoreo de salud detenido correctamente")
        except Exception as e:
            logging.error(f"Error al detener el servicio de monitoreo: {e}")


def main():
    health_service = HealthCheckService()
    try:
        health_service.start()
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        health_service.stop()


if __name__ == "__main__":
    main()