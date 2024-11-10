import threading
import time
import zmq
import json
import logging
import os
from datetime import datetime

class GridVisualizer:
    def __init__(self, N=10, M=10, update_interval=0.5):
        self.N = N
        self.M = M
        self.update_interval = update_interval
        self.taxis = {}  # {id_taxi: {'pos': (x,y), 'status': 'status', 'last_update': timestamp}}
        self.users = {}  # {id_user: {'pos': (x,y), 'timestamp': timestamp}}
        self.running = True

        # Configurar ZMQ
        self.context = zmq.Context()
        self.subscriber = self.context.socket(zmq.SUB)
        self.subscriber.connect("tcp://localhost:5558")

        # Suscribirse a todos los tópicos relevantes
        topics = ["posicion_taxi", "solicitud_servicio", "resultado_servicio"]
        for topic in topics:
            self.subscriber.setsockopt_string(zmq.SUBSCRIBE, topic)

        # Configurar logging
        self.logger = logging.getLogger('GridVisualizer')
        self.logger.setLevel(logging.INFO)
        formatter = logging.Formatter('🗺️ %(asctime)s - %(message)s')
        ch = logging.StreamHandler()
        ch.setFormatter(formatter)
        self.logger.addHandler(ch)

    def clear_screen(self):
        os.system('cls' if os.name == 'nt' else 'clear')

    def _update_taxi(self, data):
        """Actualiza la posición de un taxi"""
        taxi_id = data['id_taxi']
        pos = (int(data['posicion']['lat']), int(data['posicion']['lng']))
        status = data['estado']
        # Verificar que la posición esté dentro de los límites
        if 0 <= pos[0] < self.N and 0 <= pos[1] < self.M:
            self.taxis[taxi_id] = {
                'pos': pos,
                'status': status,
                'last_update': time.time()
            }
            self.logger.info(f"🚕 Taxi {taxi_id[:6]} en posición {pos} - Estado: {status}")
        else:
            self.logger.warning(f"⚠️ Posición inválida para taxi {taxi_id[:6]}: {pos}")

    def _update_user(self, data):
        """Actualiza la posición de un usuario"""
        user_id = data['id_cliente']
        pos = (int(data['posicion']['lat']), int(data['posicion']['lng']))
        # Verificar que la posición esté dentro de los límites
        if 0 <= pos[0] < self.N and 0 <= pos[1] < self.M:
            self.users[user_id] = {
                'pos': pos,
                'timestamp': time.time()
            }
            self.logger.info(f"👤 Usuario {user_id[:6]} en posición {pos}")
        else:
            self.logger.warning(f"⚠️ Posición inválida para usuario {user_id[:6]}: {pos}")

    def _get_active_taxis(self):
        """Retorna solo los taxis que han actualizado su posición en los últimos 60 segundos"""
        current_time = time.time()
        return {
            taxi_id: info
            for taxi_id, info in self.taxis.items()
            if current_time - info['last_update'] < 60
        }

    def _get_active_users(self):
        """Retorna solo los usuarios activos en los últimos 60 segundos"""
        current_time = time.time()
        return {
            user_id: info
            for user_id, info in self.users.items()
            if current_time - info['timestamp'] < 60
        }

    def _update_visualization(self):
        """Actualiza la visualización del grid"""
        while self.running:
            try:
                self.clear_screen()

                # Crear grid vacío con espacios adecuados
                grid = [[' · ' for _ in range(self.M)] for _ in range(self.N)]

                # Obtener taxis y usuarios activos
                active_taxis = self._get_active_taxis()
                active_users = self._get_active_users()

                # Colocar taxis en el grid
                for taxi_id, info in active_taxis.items():
                    x, y = info['pos']
                    if 0 <= x < self.N and 0 <= y < self.M:
                        grid[x][y] = ' 🚕 ' if info['status'] == 'disponible' else ' 🚖 '

                # Colocar usuarios en el grid
                for user_id, info in active_users.items():
                    x, y = info['pos']
                    if 0 <= x < self.N and 0 <= y < self.M:
                        if grid[x][y].strip() in ['🚕', '🚖']:
                            grid[x][y] = '🚖👤'
                        else:
                            grid[x][y] = ' 👤 '

                # Imprimir el grid con formato mejorado
                print("\n=== 🗺️ Sistema de Taxis 🗺️ ===")
                # Imprimir números de columna
                print("    " + "".join(f"{i:3d}" for i in range(self.M)))
                print("   " + "─" * (self.M * 3 + 1))

                # Imprimir filas con números y contenido
                for i in range(self.N):
                    print(f"{i:2d} │", end="")
                    for j in range(self.M):
                        print(f"{grid[i][j]}", end="")
                    print("│")
                print("   " + "─" * (self.M * 3 + 1))

                # Imprimir estadísticas
                print("\n📊 Estadísticas:")
                print(f"🚕 Taxis activos: {len(active_taxis)}")
                print(f"   - Disponibles: {sum(1 for t in active_taxis.values() if t['status'] == 'disponible')}")
                print(f"   - Ocupados: {sum(1 for t in active_taxis.values() if t['status'] != 'disponible')}")
                print(f"👤 Usuarios activos: {len(active_users)}")

                # Imprimir leyenda
                print("\n🔍 Leyenda:")
                print("🚕 : Taxi Disponible")
                print("🚖 : Taxi Ocupado")
                print("👤 : Usuario")
                print("· : Espacio vacío")

                time.sleep(self.update_interval)

            except Exception as e:
                self.logger.error(f"Error en visualización: {e}")
                continue

    def _process_messages(self):
        while self.running:
            try:
                message = self.subscriber.recv_string(flags=zmq.NOBLOCK)
                topic, data = message.split(" ", 1)
                data = json.loads(data)

                if topic == "posicion_taxi":
                    self._update_taxi(data)
                elif topic == "solicitud_servicio":
                    self._update_user(data)
                elif topic == "resultado_servicio":
                    if data.get('subtipo') == 'confirmacion_servicio':
                        self._handle_service_confirmation(data)

            except zmq.Again:
                time.sleep(0.1)
            except Exception as e:
                self.logger.error(f"Error procesando mensaje: {e}")

    def start(self):
        self.logger.info("Iniciando visualizador de grid")
        self.viz_thread = threading.Thread(target=self._update_visualization)
        self.zmq_thread = threading.Thread(target=self._process_messages)
        self.viz_thread.start()
        self.zmq_thread.start()

    def stop(self):
        self.running = False
        self.viz_thread.join()
        self.zmq_thread.join()
        self.subscriber.close()
        self.context.term()
        self.logger.info("Visualizador detenido")

def main():
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s'
    )

    print("=== 🗺️ Visualizador del Sistema de Taxis 🗺️ ===")
    N = int(input("📏 Ingrese el tamaño N de la cuadrícula: "))
    M = int(input("📏 Ingrese el tamaño M de la cuadrícula: "))

    visualizer = GridVisualizer(N, M)
    try:
        visualizer.start()
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        visualizer.stop()

if __name__ == "__main__":
    main()