import logging
from concurrent import futures
from datetime import datetime
from threading import Thread, Event

import grpc
import zmq
import time

import broker_service_pb2
import broker_service_pb2_grpc


class TaxiBroker(broker_service_pb2_grpc.BrokerServiceServicer):
    def __init__(self, frontend_port=5557, backend_port=5558, grpc_port=50053):
        self.monitor_thread = None
        self.proxy_thread = None
        self.context = zmq.Context()
        self.active = True
        self.stop_event = Event()
        self.active_subscribers = set()  # Agregado el conjunto de suscriptores activos
        self.grpc_port = grpc_port

        # Socket para recibir mensajes de publicadores
        self.frontend = self.context.socket(zmq.XSUB)
        self.frontend.bind(f"tcp://*:{frontend_port}")

        # Socket para distribuir mensajes a suscriptores
        self.backend = self.context.socket(zmq.XPUB)
        self.backend.bind(f"tcp://*:{backend_port}")

        # Configuración importante para el backend
        self.backend.setsockopt(zmq.XPUB_VERBOSE, 1)
        self.backend.setsockopt(zmq.LINGER, 0)
        self.frontend.setsockopt(zmq.LINGER, 0)

        # Aumentar el high water mark para evitar pérdida de mensajes
        self.frontend.setsockopt(zmq.SNDHWM, 1000000)
        self.frontend.setsockopt(zmq.RCVHWM, 1000000)
        self.backend.setsockopt(zmq.SNDHWM, 1000000)
        self.backend.setsockopt(zmq.RCVHWM, 1000000)

        logging.info(f"Broker iniciado - Frontend: {frontend_port}, Backend: {backend_port}")

    def HealthCheck(self, request, context):
        try:
            return broker_service_pb2.HealthCheckResponse(
                status=True,
                message="Broker is healthy",
                timestamp=datetime.now().isoformat()
            )
        except Exception as e:
            return broker_service_pb2.HealthCheckResponse(
                status=False,
                message=f"Health check failed: {str(e)}",
                timestamp=datetime.now().isoformat()
            )

    def _monitor_subscriptions(self):
        """Monitorea las suscripciones y desuscripciones"""
        poller = zmq.Poller()
        poller.register(self.backend, zmq.POLLIN)

        while not self.stop_event.is_set():
            try:
                socks = dict(poller.poll(timeout=1000))
                if self.backend in socks:
                    event = self.backend.recv()
                    if event[0] == 1:  # Suscripción
                        topic = event[1:].decode('utf-8')
                        self.active_subscribers.add(topic)
                        logging.info(f"Nueva suscripción: {topic}")
                    elif event[0] == 0:  # Desuscripción
                        topic = event[1:].decode('utf-8')
                        self.active_subscribers.discard(topic)
                        logging.info(f"Desuscripción: {topic}")
            except Exception as e:
                logging.error(f"Error en monitoreo de suscripciones: {e}")
                if not self.stop_event.is_set():
                    continue

    def _forward_messages(self):
        """Reenvía mensajes del frontend al backend"""
        try:
            zmq.proxy(self.frontend, self.backend)
        except zmq.error.ZMQError as e:
            if not self.stop_event.is_set():
                logging.error(f"Error en proxy ZMQ: {e}")

    def start(self):
        """Inicia el broker y el monitoreo de suscripciones"""
        try:
            # Iniciar thread de monitoreo
            self.monitor_thread = Thread(target=self._monitor_subscriptions)
            self.monitor_thread.daemon = True
            self.monitor_thread.start()

            # Iniciar thread de reenvío de mensajes
            self.proxy_thread = Thread(target=self._forward_messages)
            self.proxy_thread.daemon = True
            self.proxy_thread.start()

            # Iniciar servidor gRPC para Health Check
            grpc_server = grpc.server(futures.ThreadPoolExecutor(max_workers=10))
            broker_service_pb2_grpc.add_BrokerServiceServicer_to_server(self, grpc_server)
            grpc_server.add_insecure_port(f'[::]:{self.grpc_port}')
            grpc_server.start()
            logging.info(f'gRPC server started on port {self.grpc_port}')

            # Mantener el broker activo
            while not self.stop_event.is_set():
                time.sleep(1)

        except KeyboardInterrupt:
            logging.info("Broker detenido por el usuario")
            self.stop()
        except Exception as e:
            logging.error(f"Error al iniciar el broker: {e}")
            self.stop()

    def stop(self):
        """Detiene el broker y libera recursos"""
        logging.info("Deteniendo broker...")
        self.stop_event.set()

        # Esperar a que terminen los threads
        if hasattr(self, 'monitor_thread'):
            self.monitor_thread.join(timeout=2)
        if hasattr(self, 'proxy_thread'):
            self.proxy_thread.join(timeout=2)

        # Cerrar sockets
        self.frontend.close()
        self.backend.close()
        self.context.term()
        logging.info("Broker detenido")


def main():
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s'
    )

    broker = TaxiBroker()
    try:
        broker.start()
    except KeyboardInterrupt:
        broker.stop()


if __name__ == "__main__":
    main()
