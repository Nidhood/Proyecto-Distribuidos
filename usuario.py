import zmq
import json
import logging
import time
import uuid
from datetime import datetime

class UsuarioNode:
    def __init__(self, posicion, puerto_pub=5557, puerto_sub=5558):
        self.id_usuario = str(uuid.uuid4())
        self.posicion = posicion

        # Configurar logging personalizado
        self.logger = logging.getLogger(f'Usuario-{self.id_usuario[:6]}')
        self.logger.setLevel(logging.INFO)
        formatter = logging.Formatter('👤 %(asctime)s - %(message)s')
        ch = logging.StreamHandler()
        ch.setFormatter(formatter)
        self.logger.addHandler(ch)

        # Configurar ZMQ
        self.context = zmq.Context()
        self.publicador = self.context.socket(zmq.PUB)
        self.publicador.connect(f"tcp://localhost:{puerto_pub}")

        self.suscriptor = self.context.socket(zmq.SUB)
        self.suscriptor.connect(f"tcp://localhost:{puerto_sub}")
        self.suscriptor.setsockopt_string(zmq.SUBSCRIBE, "resultado_servicio")

        self.logger.info(f"📍 Iniciando en posición ({self.posicion['lat']}, {self.posicion['lng']})")
        time.sleep(1)  # Dar tiempo para establecer conexiones
        self.logger.info("🔌 Conexiones establecidas")

    def solicitar_taxi(self):
        mensaje = {
            'tipo': 'solicitud_servicio',
            'id_cliente': self.id_usuario,
            'posicion': self.posicion,
            'timestamp': time.time()
        }
        self.publicador.send_string(f"solicitud_servicio {json.dumps(mensaje)}")
        self.logger.info("🚖 Solicitando taxi...")

        # Esperar respuesta
        try:
            topic, respuesta = self.suscriptor.recv_string().split(" ", 1)
            respuesta = json.loads(respuesta)

            if respuesta['tipo'] == 'resultado_servicio':
                if respuesta.get('subtipo') == 'error':
                    self.logger.warning(f"❌ Solicitud rechazada: {respuesta['mensaje']}")
                    return False
                elif respuesta.get('subtipo') == 'confirmacion_servicio':
                    taxi_id = respuesta['id_taxi']
                    taxi_pos = respuesta['posicion_taxi']
                    self.logger.info(f"✅ Taxi {taxi_id[:6]} asignado")
                    self.logger.info(f"📍 Posición del taxi: ({taxi_pos['lat']}, {taxi_pos['lng']})")

                    # Simular espera del servicio
                    self.logger.info("🕐 Iniciando viaje...")
                    time.sleep(30)  # 30 segundos de viaje
                    self.logger.info("🏁 Viaje completado")
                    return True

        except zmq.ZMQError as e:
            self.logger.error(f"❌ Error en la comunicación: {e}")
            return False
        except Exception as e:
            self.logger.error(f"❌ Error inesperado: {e}")
            return False

def main():
    print("=== 👤 Sistema de Solicitud de Taxi 👤 ===")
    pos_x = int(input("📍 Ingrese su posición X: "))
    pos_y = int(input("📍 Ingrese su posición Y: "))

    usuario = UsuarioNode(
        posicion={'lat': pos_x, 'lng': pos_y}
    )
    usuario.solicitar_taxi()

if __name__ == "__main__":
    main()