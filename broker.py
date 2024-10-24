import zmq
import logging
import time
from threading import Thread


class TaxiBroker:
    def __init__(self, frontend_port=5559, backend_port=5560):
        self.context = zmq.Context()

        # Socket facing clients/taxis (publishers)
        self.frontend = self.context.socket(zmq.XPUB)
        self.frontend.bind(f"tcp://*:{frontend_port}")

        # Socket facing services (subscribers)
        self.backend = self.context.socket(zmq.XSUB)
        self.backend.bind(f"tcp://*:{backend_port}")

        # Socket for broker health check
        self.health_check = self.context.socket(zmq.REP)
        self.health_check.bind("tcp://*:5561")

        self.active = True
        logging.info(f"Broker started - Frontend: {frontend_port}, Backend: {backend_port}")

        # Mantener registro de suscriptores activos
        self.active_subscribers = set()
        self.frontend.setsockopt(zmq.XPUB_VERBOSE, 1)

    def _monitor_subscriptions(self):
        while self.active:
            try:
                event = self.frontend.recv()
                if event[0] == 1:  # Subscription event
                    self.active_subscribers.add(event[1:])
                elif event[0] == 0:  # Unsubscription event
                    self.active_subscribers.discard(event[1:])
            except Exception as e:
                logging.error(f"Subscription monitoring error: {e}")

    def _health_check_handler(self):
        while self.active:
            try:
                message = self.health_check.recv_string()
                if message == "health_check":
                    self.health_check.send_string("alive")
            except Exception as e:
                logging.error(f"Health check error: {e}")

    def start(self):
        # Iniciar thread de monitoreo de suscripciones
        Thread(target=self._monitor_subscriptions, daemon=True).start()

        # Iniciar thread de health check
        Thread(target=self._health_check_handler, daemon=True).start()

        # Configurar proxy para mensajes
        zmq.proxy(self.frontend, self.backend)

    def stop(self):
        self.active = False
        self.frontend.close()
        self.backend.close()
        self.health_check.close()
        self.context.term()


class BackupBroker(TaxiBroker):
    def __init__(self, main_broker_port=5561):
        super().__init__(frontend_port=6559, backend_port=6560)
        self.main_broker_port = main_broker_port
        self.is_primary = False

    def monitor_main_broker(self):
        context = zmq.Context()
        health_check = context.socket(zmq.REQ)
        health_check.connect(f"tcp://localhost:{self.main_broker_port}")

        while True:
            try:
                health_check.send_string("health_check")
                health_check.recv_string(timeout=1000)  # 1 segundo timeout
                if self.is_primary:
                    logging.info("Main broker is back online, switching to backup mode")
                    self.is_primary = False
            except zmq.error.Again:
                if not self.is_primary:
                    logging.warning("Main broker not responding, taking over as primary")
                    self.is_primary = True
            time.sleep(2)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    # Iniciar broker principal
    broker = TaxiBroker()


    # Iniciar broker de respaldo
    backup_broker = BackupBroker()

    try:
        Thread(target=backup_broker.monitor_main_broker, daemon=True).start()
        broker.start()
    except KeyboardInterrupt:
        broker.stop()
        backup_broker.stop()
