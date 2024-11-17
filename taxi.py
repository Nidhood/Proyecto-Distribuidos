# Modificaciones en taxi.py

import json
import logging
import threading
import time
import uuid
import zmq
from datetime import datetime
import math


class TaxiNode:
    def __init__(self, N, M, posicion_inicial, velocidad, num_servicios, puerto_pub=5557, puerto_sub=5558):
        self.id_taxi = str(uuid.uuid4())
        self.N, self.M = N, M
        self.posicion_inicial = posicion_inicial
        self.posicion = posicion_inicial.copy()
        self.velocidad = velocidad
        self.num_servicios_max = num_servicios
        self.servicios_realizados = 0
        self.estado = 'AVAILABLE'
        self.activo = True
        self.destino = None
        self.servicio_actual = None
        self.registro_confirmado = False

        # Configurar logging
        self.logger = logging.getLogger(f'Taxi-{self.id_taxi[:6]}')
        self.logger.setLevel(logging.INFO)
        formatter = logging.Formatter('🚕 %(asctime)s - %(message)s')
        ch = logging.StreamHandler()
        ch.setFormatter(formatter)
        self.logger.addHandler(ch)

        # Configuración ZMQ
        self.context = zmq.Context()
        self.publisher = self.context.socket(zmq.PUB)
        self.publisher.setsockopt(zmq.IMMEDIATE, 1)
        self.publisher.connect(f"tcp://localhost:{puerto_pub}")

        self.subscriber = self.context.socket(zmq.SUB)
        self.subscriber.connect(f"tcp://localhost:{puerto_sub}")
        time.sleep(0.1)
        self.subscriber.setsockopt_string(zmq.SUBSCRIBE, "asignacion_taxi")
        self.subscriber.setsockopt_string(zmq.SUBSCRIBE, "confirmacion_taxi")

        # Iniciar proceso de registro
        self.registrar_taxi()

    def calcular_distancia(self, pos1, pos2):
        """Calcula la distancia euclidiana entre dos posiciones"""
        return math.sqrt(
            (pos1['lat'] - pos2['lat']) ** 2 +
            (pos1['lng'] - pos2['lng']) ** 2
        )

    def registrar_taxi(self):
        """Registra el taxi con el servidor y espera confirmación"""
        mensaje = {
            'tipo': 'registro_taxi',
            'id_taxi': self.id_taxi,
            'posicion': self.posicion,
            'estado': self.estado,
            'timestamp': time.time()
        }
        self.publisher.send_string(f"registro_taxi {json.dumps(mensaje)}")
        self.logger.info("📝 Enviando solicitud de registro")

        # Esperar confirmación de registro
        while not self.registro_confirmado and self.activo:
            try:
                topic, mensaje = self.subscriber.recv_string().split(" ", 1)
                datos = json.loads(mensaje)
                if topic == "confirmacion_taxi" and datos.get('id_taxi') == self.id_taxi:
                    if datos.get('estado') == 'success':
                        self.registro_confirmado = True
                        self.logger.info("✅ Registro confirmado por el servidor")
                        self.logger.info(f"🚖 Taxi ID: {self.id_taxi}")
                        self.logger.info(f"📍 Posición inicial: ({self.posicion['lat']}, {self.posicion['lng']})")
                        break
                    else:
                        self.logger.error(f"❌ Error en registro: {datos.get('mensaje', 'Unknown error')}")
                        time.sleep(5)  # Esperar antes de reintentar
                        self.registrar_taxi()
            except Exception as e:
                self.logger.error(f"❌ Error en proceso de registro: {e}")
                time.sleep(5)

    def calcular_siguiente_paso(self):
        """Calcula el siguiente paso hacia el destino"""
        if not self.destino:
            return self.posicion

        distancia = self.calcular_distancia(self.posicion, self.destino)
        if distancia == 0:  # Si estamos en el destino
            return self.destino

        # Calcular vector de dirección
        dx = self.destino['lat'] - self.posicion['lat']
        dy = self.destino['lng'] - self.posicion['lng']

        # Determinar el paso en cada dirección
        if abs(dx) > abs(dy):  # Mover en la dirección dominante
            paso_lat = 1 if dx > 0 else -1
            paso_lng = 0
        elif abs(dy) > abs(dx):
            paso_lat = 0
            paso_lng = 1 if dy > 0 else -1
        else:  # Mover diagonalmente si están iguales
            paso_lat = 1 if dx > 0 else -1
            paso_lng = 1 if dy > 0 else -1

        nueva_lat = self.posicion['lat'] + paso_lat
        nueva_lng = self.posicion['lng'] + paso_lng

        # Asegurar que no nos salimos de la cuadrícula
        nueva_lat = max(0, min(self.N - 1, nueva_lat))
        nueva_lng = max(0, min(self.M - 1, nueva_lng))

        return {'lat': nueva_lat, 'lng': nueva_lng}

    def mover_taxi(self):
        """Maneja el movimiento del taxi"""
        while self.activo and self.servicios_realizados < self.num_servicios_max:
            if self.estado == 'BUSY' and self.destino:
                nueva_posicion = self.calcular_siguiente_paso()
                if nueva_posicion != self.posicion:
                    self.posicion = nueva_posicion
                    self.actualizar_posicion()
                    self.logger.info(f"🚖 Moviendo hacia ({nueva_posicion['lat']}, {nueva_posicion['lng']})")

                # Verificar si llegamos al destino
                if self.calcular_distancia(self.posicion, self.destino) == 0:
                    self.logger.info("🎯 Llegado al destino")
                    if self.servicio_actual:
                        self.completar_servicio()

            time.sleep(1)  # Actualizar cada segundo

    def actualizar_posicion(self):
        """Envía actualización de posición al servidor"""
        mensaje = {
            'tipo': 'posicion_taxi',
            'id_taxi': self.id_taxi,
            'posicion': self.posicion,
            'estado': self.estado,
            'timestamp': time.time()
        }
        self.publisher.send_string(f"posicion_taxi {json.dumps(mensaje)}")

    def completar_servicio(self):
        """Maneja la finalización de un servicio"""
        self.servicios_realizados += 1
        if self.servicios_realizados >= self.num_servicios_max:
            self.logger.info("🏁 Completados todos los servicios asignados")
            self.estado = 'OFFLINE'
            self.activo = False
        else:
            self.estado = 'AVAILABLE'
            self.destino = None
            self.servicio_actual = None
            self.actualizar_posicion()
            self.logger.info(f"✅ Servicio completado ({self.servicios_realizados}/{self.num_servicios_max})")

    def procesar_mensajes(self):
        """Procesa mensajes recibidos del servidor"""
        while self.activo:
            try:
                topic, mensaje = self.subscriber.recv_string().split(" ", 1)
                datos = json.loads(mensaje)

                if topic == "asignacion_taxi" and datos.get('id_taxi') == self.id_taxi:
                    if self.estado == 'AVAILABLE':
                        self.estado = 'BUSY'
                        self.destino = datos['posicion_cliente']
                        self.servicio_actual = datos['id_servicio']
                        self.logger.info(f"🎯 Nuevo servicio asignado: {datos['id_servicio']}")
                        self.logger.info(f"📍 Destino: ({self.destino['lat']}, {self.destino['lng']})")

            except Exception as e:
                self.logger.error(f"❌ Error procesando mensaje: {e}")

    def start(self):
        """Inicia los hilos del taxi"""
        if not self.registro_confirmado:
            self.logger.error("❌ No se puede iniciar sin confirmación de registro")
            return

        self.logger.info(f"🎮 Iniciando servicio con velocidad {self.velocidad} km/h")
        threading.Thread(target=self.mover_taxi, daemon=True).start()
        threading.Thread(target=self.procesar_mensajes, daemon=True).start()

    def stop(self):
        """Detiene el taxi"""
        self.estado = 'OFFLINE'
        self.activo = False
        self.subscriber.close()
        self.publisher.close()
        self.context.term()
        self.logger.info("🛑 Taxi detenido")


def main():
    print("=== 🚕 Iniciando nuevo taxi 🚕 ===")
    N = int(input("📏 Ingrese el tamaño N de la cuadrícula: "))
    M = int(input("📏 Ingrese el tamaño M de la cuadrícula: "))
    pos_x = float(input("📍 Ingrese la posición inicial X: "))
    pos_y = float(input("📍 Ingrese la posición inicial Y: "))
    velocidad = float(input("🚀 Ingrese la velocidad en km/h: "))
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
        print("\n🛑 Programa terminado por el usuario")
    finally:
        taxi.stop()


if __name__ == "__main__":
    main()
