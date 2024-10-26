import zmq
import json
import logging
import time
import uuid


class UsuarioNode:
    def __init__(self, posicion, puerto_pub=5557, puerto_sub=5558):
        self.id_usuario = str(uuid.uuid4())
        self.posicion = posicion
        self.context = zmq.Context()

        # Socket para publicar mensajes
        self.publicador = self.context.socket(zmq.PUB)
        self.publicador.connect(f"tcp://localhost:{puerto_pub}")

        # Socket para suscribirse a mensajes
        self.suscriptor = self.context.socket(zmq.SUB)
        self.suscriptor.connect(f"tcp://localhost:{puerto_sub}")
        self.suscriptor.setsockopt_string(zmq.SUBSCRIBE, "resultado_servicio")
        logging.info(f"[USUARIO] Conectando a puertos - PUB: {puerto_pub}, SUB: {puerto_sub}")
        time.sleep(1)
        logging.info("[USUARIO] Conexiones establecidas")

    def solicitar_taxi(self):
        mensaje = {
            'tipo': 'solicitud_servicio',
            'id_cliente': self.id_usuario,
            'posicion': self.posicion,
            'timestamp': time.time()
        }
        self.publicador.send_string(f"solicitud_servicio {json.dumps(mensaje)}")
        logging.info(f"Usuario {self.id_usuario} solicitó un taxi en {self.posicion}")

        # Esperar respuesta
        try:
            topic, respuesta = self.suscriptor.recv_string().split(" ", 1)
            respuesta = json.loads(respuesta)

            if respuesta['tipo'] == 'error':
                logging.warning(f"Solicitud rechazada: {respuesta['mensaje']}")
                return False
            elif respuesta['tipo'] == 'confirmacion_servicio':
                logging.info(f"Taxi {respuesta['id_taxi']} asignado al servicio {respuesta['id_servicio']}")
                # Esperar 30 segundos simulando el viaje
                time.sleep(30)
                return True

        except zmq.ZMQError as e:
            logging.error(f"Error al recibir respuesta: {e}")
            return False


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    pos_x = int(input("Ingrese la posición X del usuario: "))
    pos_y = int(input("Ingrese la posición Y del usuario: "))

    usuario = UsuarioNode(
        posicion={'lat': pos_x, 'lng': pos_y}
    )
    usuario.solicitar_taxi()
