import json
import logging
import threading
import time
import uuid

import zmq

class TaxiNode:
    def __init__(self, N, M, posicion_inicial, velocidad, num_servicios, puerto_pub=5557, puerto_sub=5558):
        self.id_taxi = str(uuid.uuid4())
        self.N, self.M = N, M
        self.posicion_inicial = posicion_inicial
        self.posicion = posicion_inicial.copy()
        self.velocidad = velocidad
        self.num_servicios_max = num_servicios
        self.servicios_realizados = 0
        self.estado = 'disponible'
        self.activo = True

        # Configuración ZMQ
        self.context = zmq.Context()
        self.publisher = self.context.socket(zmq.PUB)
        self.publisher.connect(f"tcp://localhost:{puerto_pub}")

        self.subscriber = self.context.socket(zmq.SUB)
        self.subscriber.connect(f"tcp://localhost:{puerto_sub}")
        self.subscriber.setsockopt_string(zmq.SUBSCRIBE, "asignacion_taxi")

        # Registro inicial
        self.registrar_taxi()
        logging.info(f"Taxi {self.id_taxi} iniciado en posición {self.posicion}")

    def registrar_taxi(self):
        mensaje = {
            'tipo': 'registro_taxi',
            'id_taxi': self.id_taxi,
            'posicion': self.posicion,
            'estado': self.estado,
            'timestamp': time.time()
        }
        self.publisher.send_string(f"registro_taxi {json.dumps(mensaje)}")

    def calcular_nueva_posicion(self):
        nueva_posicion = {
            'lat': min(max(0, self.posicion['lat'] + 1), self.N),
            'lng': min(max(0, self.posicion['lng'] + 1), self.M)
        }
        return nueva_posicion

    def mover_taxi(self):
        while self.activo and self.servicios_realizados < self.num_servicios_max:
            if self.estado == 'disponible':
                nueva_posicion = self.calcular_nueva_posicion()
                self.posicion = nueva_posicion
                self.actualizar_posicion()
                time.sleep(30 / self.velocidad)

    def actualizar_posicion(self):
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
                topic, mensaje = self.subscriber.recv_string().split(" ", 1)
                datos = json.loads(mensaje)

                if datos.get('id_taxi') == self.id_taxi and datos.get('tipo') == 'asignacion_servicio':
                    self.estado = 'ocupado'
                    logging.info(f"Taxi {self.id_taxi} asignado a servicio {datos['id_servicio']}")

                    # Simular servicio (30 segundos)
                    time.sleep(30)

                    self.servicios_realizados += 1
                    if self.servicios_realizados >= self.num_servicios_max:
                        self.activo = False
                        logging.info(f"Taxi {self.id_taxi} completó todos sus servicios")
                    else:
                        # Volver a posición inicial
                        self.posicion = self.posicion_inicial.copy()
                        self.estado = 'disponible'
                        self.actualizar_posicion()

            except Exception as e:
                logging.error(f"Error al procesar mensaje: {e}")

    def start(self):
        threading.Thread(target=self.mover_taxi).start()
        threading.Thread(target=self.procesar_mensajes).start()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    N = int(input("Ingrese el tamaño N de la cuadrícula: "))
    M = int(input("Ingrese el tamaño M de la cuadrícula: "))
    pos_x = int(input("Ingrese la posición inicial X: "))
    pos_y = int(input("Ingrese la posición inicial Y: "))
    velocidad = int(input("Ingrese la velocidad en km/h: "))
    num_servicios = int(input("Ingrese el número máximo de servicios: "))

    taxi = TaxiNode(
        N=N,
        M=M,
        posicion_inicial={'lat': pos_x, 'lng': pos_y},
        velocidad=velocidad,
        num_servicios=num_servicios
    )
    taxi.start()