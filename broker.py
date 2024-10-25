import logging
from threading import Thread, Event

import zmq


class TaxiBroker:
    def __init__(self, frontend_port=5559, backend_port=5556):
        """
        Inicializa el broker con los puertos especificados.
        frontend_port: Puerto para publicadores (clientes/taxis)
        backend_port: Puerto para suscriptores (servidor)
        """
        self.context = zmq.Context()
        self.active = True
        self.stop_event = Event()

        # Socket XPUB para recibir mensajes de publicadores
        self.frontend = self.context.socket(zmq.XPUB)
        self.frontend.bind(f"tcp://*:{frontend_port}")
        self.frontend.setsockopt(zmq.XPUB_VERBOSE, 1)

        # Socket XSUB para distribuir mensajes a suscriptores
        self.backend = self.context.socket(zmq.XSUB)
        self.backend.bind(f"tcp://*:{backend_port}")

        # Set de suscriptores activos para monitoreo
        self.active_subscribers = set()

        logging.info(f"Broker iniciado - Frontend: {frontend_port}, Backend: {backend_port}")

    def _monitor_subscriptions(self):
        """Monitorea las suscripciones y desuscripciones"""
        poller = zmq.Poller()
        poller.register(self.frontend, zmq.POLLIN)

        while not self.stop_event.is_set():
            try:
                socks = dict(poller.poll(timeout=1000))
                if self.frontend in socks:
                    event = self.frontend.recv()
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

    def start(self):
        """Inicia el broker y el monitoreo de suscripciones"""
        try:
            # Iniciar thread de monitoreo
            self.monitor_thread = Thread(target=self._monitor_subscriptions)
            self.monitor_thread.daemon = True
            self.monitor_thread.start()

            # Configurar proxy ZMQ
            zmq.proxy(self.frontend, self.backend)
        except Exception as e:
            logging.error(f"Error al iniciar el broker: {e}")
            self.stop()

    def stop(self):
        """Detiene el broker y libera recursos"""
        logging.info("Deteniendo broker...")
        self.stop_event.set()

        # Esperar a que termine el thread de monitoreo
        if hasattr(self, 'monitor_thread'):
            self.monitor_thread.join(timeout=2)

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