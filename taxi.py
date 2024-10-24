import zmq
import json
import logging
from threading import Thread
import uuid
import time


class Taxi:
    def __init__(self, id_taxi=None, puerto_pub=5560, puerto_sub=5559):
        self.id_taxi = id_taxi or str(uuid.uuid4())
        self.context = zmq.Context()
        self.publisher = self.context.socket(zmq.PUB)
        self.subscriber = self.context.socket(zmq.SUB)
        self.publisher.connect(f"tcp://localhost:{puerto_pub}")
        self.subscriber.connect(f"tcp://localhost:{puerto_sub}")
        self.subscriber.setsockopt_string(zmq.SUBSCRIBE, "asignacion_taxi")
        self.posicion = {'lat': 0, 'lng': 0}
        self.estado = 'disponible'
        self.activo = True
        logging.info(f"Taxi {self.id_taxi} iniciado")

    def actualizar_posicion(self, nueva_posicion):
        self.posicion = nueva_posicion
        mensaje = {
            'tipo': 'posicion_taxi',
            'id_taxi': self.id_taxi,
            'posicion': self.posicion,
            'estado': self.estado,
            'timestamp': time.time()
        }
        self.publisher.send_string(f"posicion_taxi {json.dumps(mensaje)}")

    def procesar_mensajes(self):
        while self.activo:
            try:
                topico, mensaje = self.subscriber.recv_string().split(" ", 1)
                datos = json.loads(mensaje)
                if datos.get('id_taxi') == self.id_taxi:
                    if datos.get('tipo') == 'asignacion_servicio':
                        self.estado = 'ocupado'
                        logging.info(f"Servicio asignado - ID: {datos['id_servicio']}")
                        self.confirmar_servicio(datos['id_servicio'])
            except Exception as e:
                logging.error(f"Error procesando mensaje: {e}")

    def confirmar_servicio(self, id_servicio):
        mensaje = {
            'tipo': 'confirmacion_taxi',
            'id_taxi': self.id_taxi,
            'id_servicio': id_servicio,
            'timestamp': time.time()
        }
        self.publisher.send_string(f"confirmacion_servicio {json.dumps(mensaje)}")

    def start(self):
        Thread(target=self.procesar_mensajes).start()
        Thread(target=self.actualizacion_periodica).start()

    def actualizacion_periodica(self):
        while self.activo:
            self.actualizar_posicion(self.posicion)
            time.sleep(5)  # Actualizar cada 5 segundos

    def stop(self):
        self.activo = False
        self.publisher.close()
        self.subscriber.close()
        self.context.term()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    taxi = Taxi()
    taxi.start()

    # Ejemplo de actualización de posición
    nueva_posicion = {'lat': 4.624335, 'lng': -74.063644}
    taxi.actualizar_posicion(nueva_posicion)

