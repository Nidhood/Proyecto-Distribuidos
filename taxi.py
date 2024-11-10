import json
import logging
import threading
import time
import uuid
import zmq
from datetime import datetime

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

        # Configurar logging personalizado
        self.logger = logging.getLogger(f'Taxi-{self.id_taxi[:6]}')
        self.logger.setLevel(logging.INFO)
        formatter = logging.Formatter('🚕 %(asctime)s - %(message)s')
        ch = logging.StreamHandler()
        ch.setFormatter(formatter)
        self.logger.addHandler(ch)

        # Configuración ZMQ
        self.context = zmq.Context()
        self.publisher = self.context.socket(zmq.PUB)
        self.publisher.connect(f"tcp://localhost:{puerto_pub}")

        self.subscriber = self.context.socket(zmq.SUB)
        self.subscriber.connect(f"tcp://localhost:{puerto_sub}")
        self.subscriber.setsockopt_string(zmq.SUBSCRIBE, "asignacion_taxi")
        self.subscriber.setsockopt_string(zmq.SUBSCRIBE, "confirmacion_taxi")

        # Registro inicial
        self.registrar_taxi()
        self.logger.info(f"🚀 Iniciando en posición ({self.posicion['lat']}, {self.posicion['lng']})")

    def registrar_taxi(self):
        mensaje = {
            'tipo': 'registro_taxi',
            'id_taxi': self.id_taxi,
            'posicion': self.posicion,
            'estado': self.estado,
            'timestamp': time.time()
        }
        self.publisher.send_string(f"registro_taxi {json.dumps(mensaje)}")
        self.logger.info("📝 Enviando solicitud de registro")

    def calcular_nueva_posicion(self):
        """Calcula la siguiente posición del taxi de manera cíclica en la cuadrícula"""
        nueva_lat = self.posicion['lat']
        nueva_lng = self.posicion['lng']

        # Si llegamos al límite, volvemos al inicio
        if nueva_lat >= self.N and nueva_lng >= self.M:
            nueva_lat = 0
            nueva_lng = 0
        else:
            # Incrementar primero longitud
            if nueva_lng < self.M - 1:
                nueva_lng += 1
            else:
                nueva_lng = 0
                if nueva_lat < self.N - 1:
                    nueva_lat += 1
                else:
                    nueva_lat = 0

        return {
            'lat': nueva_lat,
            'lng': nueva_lng
        }

    def actualizar_posicion(self):
        mensaje = {
            'tipo': 'posicion_taxi',
            'id_taxi': self.id_taxi,
            'posicion': self.posicion,
            'estado': self.estado,
            'timestamp': time.time()
        }
        self.publisher.send_string(f"posicion_taxi {json.dumps(mensaje)}")

    def mover_taxi(self):
        while self.activo and self.servicios_realizados < self.num_servicios_max:
            if self.estado == 'disponible':
                nueva_posicion = self.calcular_nueva_posicion()
                pos_anterior = self.posicion.copy()
                self.posicion = nueva_posicion
                self.actualizar_posicion()
                self.logger.info(f"🚖 Movimiento: ({pos_anterior['lat']}, {pos_anterior['lng']}) → ({nueva_posicion['lat']}, {nueva_posicion['lng']})")
                time.sleep(30 / self.velocidad)  # Ajustar según velocidad

    def procesar_mensajes(self):
        while self.activo:
            try:
                topic, mensaje = self.subscriber.recv_string().split(" ", 1)
                datos = json.loads(mensaje)

                if topic == "confirmacion_taxi":
                    if datos.get('id_taxi') == self.id_taxi:
                        estado = datos.get('estado', 'unknown')
                        if estado == 'success':
                            self.logger.info("✅ Registro confirmado por el servidor")
                        else:
                            self.logger.error(f"❌ Error en registro: {datos.get('mensaje', 'Unknown error')}")

                elif topic == "asignacion_taxi" and datos.get('id_taxi') == self.id_taxi:
                    self.estado = 'ocupado'
                    self.logger.info(f"🎯 Asignado a servicio {datos['id_servicio']}")
                    self.logger.info(f"🚗 Recogiendo cliente en ({datos['posicion_cliente']['lat']}, {datos['posicion_cliente']['lng']})")

                    # Simular servicio (30 segundos)
                    time.sleep(30)

                    self.servicios_realizados += 1
                    if self.servicios_realizados >= self.num_servicios_max:
                        self.logger.info("🏁 Completados todos los servicios asignados")
                        self.activo = False
                    else:
                        self.posicion = self.posicion_inicial.copy()
                        self.estado = 'disponible'
                        self.actualizar_posicion()
                        self.logger.info(f"✅ Servicio completado ({self.servicios_realizados}/{self.num_servicios_max})")
                        self.logger.info(f"🔄 Retornando a posición inicial ({self.posicion_inicial['lat']}, {self.posicion_inicial['lng']})")

            except Exception as e:
                self.logger.error(f"❌ Error procesando mensaje: {e}")

    def start(self):
        self.logger.info(f"🎮 Velocidad: {self.velocidad} km/h, Servicios máximos: {self.num_servicios_max}")
        threading.Thread(target=self.mover_taxi).start()
        threading.Thread(target=self.procesar_mensajes).start()

    def stop(self):
        self.activo = False
        self.subscriber.close()
        self.publisher.close()
        self.context.term()
        self.logger.info("🛑 Taxi detenido")

def main():
    print("=== 🚕 Iniciando nuevo taxi 🚕 ===")
    N = int(input("📏 Ingrese el tamaño N de la cuadrícula: "))
    M = int(input("📏 Ingrese el tamaño M de la cuadrícula: "))
    pos_x = int(input("📍 Ingrese la posición inicial X: "))
    pos_y = int(input("📍 Ingrese la posición inicial Y: "))
    velocidad = int(input("🚀 Ingrese la velocidad en km/h: "))
    num_servicios = int(input("🎯 Ingrese el número máximo de servicios: "))

    taxi = TaxiNode(
        N=N,
        M=M,
        posicion_inicial={'lat': pos_x, 'lng': pos_y},
        velocidad=velocidad,
        num_servicios=num_servicios
    )
    try:
        taxi.start()
        while taxi.activo:
            time.sleep(1)
    except KeyboardInterrupt:
        taxi.stop()
        print("\n🛑 Programa terminado por el usuario")
    finally:
        taxi.stop()

if __name__ == "__main__":
    main()