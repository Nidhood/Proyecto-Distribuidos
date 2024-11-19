import json
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
    def __init__(self, frontend_port=5557, backend_port=5558, grpc_port=50053, secondary_address=50055):
        self.monitor_thread = None
        self.proxy_thread = None
        self.context = zmq.Context()
        self.secondary_address = secondary_address
        self.active = True
        self.stop_event = Event()
        self.active_subscribers = set()
        self.grpc_port = grpc_port

        # Socket para recibir mensajes de publicadores (XSUB)
        self.frontend = self.context.socket(zmq.XSUB)
        self.frontend.bind(f"tcp://*:{frontend_port}")

        # Socket para distribuir mensajes a suscriptores (XPUB)
        self.backend = self.context.socket(zmq.XPUB)
        self.backend.bind(f"tcp://*:{backend_port}")

        # Configuración crucial para XPUB
        self.backend.setsockopt(zmq.XPUB_VERBOSE, 1)  # Habilitar mensajes verbosos
        self.backend.setsockopt(zmq.RCVHWM, 0)  # Sin límite de mensajes
        self.frontend.setsockopt(zmq.RCVHWM, 0)  # Sin límite de mensajes

        # Configurar keepalive
        self.backend.setsockopt(zmq.TCP_KEEPALIVE, 1)
        self.backend.setsockopt(zmq.TCP_KEEPALIVE_IDLE, 300)
        self.frontend.setsockopt(zmq.TCP_KEEPALIVE, 1)
        self.frontend.setsockopt(zmq.TCP_KEEPALIVE_IDLE, 300)

        logging.info(f"🔄 Broker iniciado - Frontend: {frontend_port}, Backend: {backend_port}")

    def _monitor_subscriptions(self):
        """Monitorea las suscripciones y desuscripciones"""
        poller = zmq.Poller()
        poller.register(self.backend, zmq.POLLIN)

        while not self.stop_event.is_set():
            try:
                socks = dict(poller.poll(timeout=100))
                if self.backend in socks:
                    event = self.backend.recv()
                    if event:  # Verificar que el evento no está vacío
                        # El primer byte indica suscripción (1) o desuscripción (0)
                        is_subscribe = event[0] == 1
                        topic = event[1:].decode('utf-8')

                        if is_subscribe:
                            self.active_subscribers.add(topic)
                            logging.info(f"📥 Nueva suscripción: {topic}")
                            # Reenviar explícitamente el mensaje de suscripción
                            self.frontend.send(event)
                        else:
                            self.active_subscribers.discard(topic)
                            logging.info(f"📤 Desuscripción: {topic}")

            except Exception as e:
                logging.error(f"❌ Error en monitoreo de suscripciones: {e}")
                if not self.stop_event.is_set():
                    time.sleep(1)  # Esperar antes de reintentar
                    continue

    def _forward_messages(self):
        """Reenvía mensajes del frontend al backend"""
        poller = zmq.Poller()
        poller.register(self.frontend, zmq.POLLIN)

        while not self.stop_event.is_set():
            try:
                socks = dict(poller.poll(timeout=100))
                if self.frontend in socks:
                    message = self.frontend.recv()
                    if message:  # Verificar mensaje no vacío
                        topic = message.split(b' ')[0]  # Extraer topic del mensaje
                        logging.debug(f"↔️ Reenviando mensaje para topic: {topic}")
                        self.backend.send(message)

            except Exception as e:
                logging.error(f"❌ Error en reenvío de mensajes: {e}")
                if not self.stop_event.is_set():
                    time.sleep(1)
                    continue
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

    def ReplicateState(self, request, context):
        self.state = json.loads(request.state)
        logging.info("Estado replicado recibido")
        return broker_service_pb2.ReplicateStateResponse(success=True)

    def replicate_state(self):
        time.sleep(4)
        state = json.dumps(self.state)
        if self.secondary_address:
            try:
                with grpc.insecure_channel(self.secondary_address) as channel:
                    stub = broker_service_pb2_grpc.BrokerServiceStub(channel)
                    request = broker_service_pb2.ReplicateStateRequest(state=state)
                    stub.ReplicateState(request)
            except Exception as e:
                logging.error(f"Error replicando estado a {self.secondary_address}: {e}")


    def start(self):
        """Inicia el broker y el monitoreo de suscripciones"""
        try:
            # Iniciar threads
            self.monitor_thread = Thread(target=self._monitor_subscriptions)
            self.monitor_thread.daemon = True
            self.monitor_thread.start()
            logging.info("📡 Monitor de suscripciones iniciado")

            self.proxy_thread = Thread(target=self._forward_messages)
            self.proxy_thread.daemon = True
            self.proxy_thread.start()
            logging.info("🔄 Proxy de mensajes iniciado")

            # Servidor gRPC
            grpc_server = grpc.server(futures.ThreadPoolExecutor(max_workers=10))
            broker_service_pb2_grpc.add_BrokerServiceServicer_to_server(self, grpc_server)
            grpc_server.add_insecure_port(f'[::]:{self.grpc_port}')
            grpc_server.start()
            logging.info(f'🚀 Servidor gRPC iniciado en puerto {self.grpc_port}')

            while not self.stop_event.is_set():
                time.sleep(1)

        except KeyboardInterrupt:
            logging.info("⛔ Broker detenido por el usuario")
            self.stop()
        except Exception as e:
            logging.error(f"❌ Error en broker: {e}")
            self.stop()

    def stop(self):
        """Detiene el broker y libera recursos"""
        logging.info("🛑 Deteniendo broker...")
        self.stop_event.set()

        if self.monitor_thread:
            self.monitor_thread.join(timeout=2)
        if self.proxy_thread:
            self.proxy_thread.join(timeout=2)

        self.frontend.close()
        self.backend.close()
        self.context.term()
        logging.info("✅ Broker detenido correctamente")


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
