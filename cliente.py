import zmq
import json
import logging
from threading import Thread
import uuid
import time


class Cliente:
    def __init__(self, id_cliente=None, puerto_pub=5560, puerto_sub=5559):
        self.id_cliente = id_cliente or str(uuid.uuid4())
        self.context = zmq.Context()
        self.publisher = self.context.socket(zmq.PUB)
        self.subscriber = self.context.socket(zmq.SUB)
        self.publisher.connect(f"tcp://localhost:{puerto_pub}")
        self.subscriber.connect(f"tcp://localhost:{puerto_sub}")
        self.subscriber.setsockopt_string(zmq.SUBSCRIBE, "resultado_servicio")
        self.activo = True
        logging.info(f"Cliente {self.id_cliente} iniciado")

    def solicitar_servicio(self, posicion):
        mensaje = {
            'tipo': 'solicitud_servicio',
            'id_cliente': self.id_cliente,
            'posicion': posicion,
            'timestamp': time.time()
        }
        self.publisher.send_string(f"solicitud_servicio {json.dumps(mensaje)}")
        logging.info(f"Cliente {self.id_cliente} solicitó servicio en posición {posicion}")

    def procesar_mensajes(self):
        while self.activo:
            try:
                topico, mensaje = self.subscriber.recv_string().split(" ", 1)
                datos = json.loads(mensaje)
                if datos.get('tipo') == 'confirmacion_servicio':
                    logging.info(f"Servicio confirmado - Taxi asignado: {datos['id_taxi']}")
                elif datos.get('tipo') == 'error':
                    logging.warning(f"Error en solicitud: {datos['mensaje']}")
            except Exception as e:
                logging.error(f"Error procesando mensaje: {e}")

    def start(self):
        Thread(target=self.procesar_mensajes).start()

    def stop(self):
        self.activo = False
        self.publisher.close()
        self.subscriber.close()
        self.context.term()



if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    cliente = Cliente()
    cliente.start()

    # Ejemplo de uso
    time.sleep(1)  # Esperar a que se establezcan las conexiones
    cliente.solicitar_servicio({'lat': 4.624335, 'lng': -74.063644})