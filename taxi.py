import json
import logging
import threading
import uuid
import zmq
import sys
import time
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
        while self.activo:
            if self.estado == 'BUSY' and self.destino and self.activo:
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
            time.sleep(5)

    def actualizar_posicion(self):
        """Actualiza la posición del taxi en el servidor"""
        mensaje = {
            'tipo': 'posicion_taxi',
            'id_taxi': self.id_taxi,
            'posicion': self.posicion,
            'estado': self.estado,
            'timestamp': time.time()
        }
        self.publisher.send_string(f"posicion_taxi {json.dumps(mensaje)}")

    def completar_servicio(self):
        """Completa el servicio actual y actualiza el estado del taxi"""
        if self.servicio_actual:
            mensaje = {
                'tipo': 'servicio_completado',
                'id_taxi': self.id_taxi,
                'posicion': self.posicion,
                'id_servicio': self.servicio_actual,
                'timestamp': time.time()
            }
            self.publisher.send_string(f"servicio_completado {json.dumps(mensaje)}")

        self.servicios_realizados += 1
        self.logger.info(f"✅ Servicio completado ({self.servicios_realizados}/{self.num_servicios_max})")

        if self.servicios_realizados >= self.num_servicios_max:
            self.logger.info("🏁 Completados todos los servicios asignados")
            self.estado = 'OFFLINE'
            self.actualizar_posicion()
            self.activo = False
        else:
            # Reiniciar estado para nuevo servicio
            self.estado = 'AVAILABLE'
            self.destino = None
            self.servicio_actual = None
            self.actualizar_posicion()

            # Volver a registrar disponibilidad
            mensaje = {
                'tipo': 'registro_taxi',
                'id_taxi': self.id_taxi,
                'posicion': self.posicion,
                'estado': self.estado,
                'timestamp': time.time()
            }
            self.publisher.send_string(f"registro_taxi {json.dumps(mensaje)}")
            self.logger.info("📝 Taxi disponible para nuevos servicios")
            self.logger.info(f"📍 Posición actual: ({self.posicion['lat']}, {self.posicion['lng']})")

    def procesar_mensajes(self):
        while self.activo:
            try:
                if not self.activo:
                    break
                topic, mensaje = self.subscriber.recv_string(flags=zmq.NOBLOCK).split(" ", 1)
                datos = json.loads(mensaje)

                if topic == "asignacion_taxi" and datos.get('id_taxi') == self.id_taxi:
                    if self.estado == 'AVAILABLE':
                        self.estado = 'BUSY'
                        self.destino = datos['posicion_cliente']
                        self.servicio_actual = datos['id_servicio']
                        self.actualizar_posicion()
                        self.logger.info(f"🎯 Nuevo servicio asignado: {datos['id_servicio']}")
                        self.logger.info(f"📍 Destino: ({self.destino['lat']}, {self.destino['lng']})")

            except zmq.Again:
                time.sleep(0.1)
            except Exception as e:
                if self.activo:
                    self.logger.error(f"❌ Error procesando mensaje: {e}")

    def start(self):
        if not self.registro_confirmado:
            self.logger.error("❌ No se puede iniciar sin confirmación de registro")
            return

        self.logger.info(f"🎮 Iniciando servicio con velocidad {self.velocidad} km/h")
        threading.Thread(target=self.mover_taxi, daemon=True).start()
        threading.Thread(target=self.procesar_mensajes, daemon=True).start()

        # Mantener el programa corriendo mientras esté activo
        while self.activo:
            time.sleep(1)

    def stop(self):
        self.logger.info("🛑 Deteniendo taxi...")
        self.estado = 'OFFLINE'
        self.actualizar_posicion()
        self.activo = False
        time.sleep(1)
        self.subscriber.close()
        self.publisher.close()
        self.context.term()
        self.logger.info("🛑 Taxi detenido correctamente")


def main():
    print("=== 🚕 Iniciando nuevo taxi 🚕 ===")

    # Verificar si los parámetros fueron pasados por la línea de comandos
    if len(sys.argv) == 7:  # Comprobar si hay 6 argumentos después del nombre del script
        N = int(sys.argv[1])
        M = int(sys.argv[2])
        pos_x = float(sys.argv[3])
        pos_y = float(sys.argv[4])
        velocidad = float(sys.argv[5])
        num_servicios = int(sys.argv[6])
    else:
        # Si no se pasaron por la línea de comandos, pedirlos al usuario
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
