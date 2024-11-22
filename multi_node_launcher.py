import random
import subprocess
import sys
import time
from typing import List, Dict, Set
import json
import os
import platform
from datetime import datetime


class MultiNodeLauncher:
    def __init__(self, grid_size_n: int, grid_size_m: int):
        self.grid_size_n = grid_size_n
        self.grid_size_m = grid_size_m
        self.used_positions: Set[tuple] = set()
        # Crear directorio para logs si no existe
        self.log_dir = "process_logs"
        os.makedirs(self.log_dir, exist_ok=True)

    def generate_unique_position(self) -> Dict[str, float]:
        """Genera una posición única en la cuadrícula"""
        while True:
            lat = random.randint(0, self.grid_size_n - 1)
            lng = random.randint(0, self.grid_size_m - 1)
            if (lat, lng) not in self.used_positions:
                self.used_positions.add((lat, lng))
                return {'lat': lat, 'lng': lng}

    def generate_taxi_data(self) -> Dict:
        """Genera datos aleatorios para un taxi"""
        velocidad = random.uniform(30.0, 80.0)
        num_servicios = 1
        posicion = self.generate_unique_position()

        return {
            'N': self.grid_size_n,
            'M': self.grid_size_m,
            'pos_x': posicion['lat'],
            'pos_y': posicion['lng'],
            'velocidad': round(velocidad, 1),
            'num_servicios': num_servicios
        }

    def generate_user_data(self) -> Dict:
        """Genera datos aleatorios para un usuario"""
        posicion = self.generate_unique_position()
        return {
            'pos_x': posicion['lat'],
            'pos_y': posicion['lng']
        }

    def create_process_window(self, cmd: List[str], process_name: str) -> subprocess.Popen:
        """Crea un nuevo proceso en una ventana separada según el sistema operativo"""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        log_file = os.path.join(self.log_dir, f"{process_name}_{timestamp}.log")

        # Obtener el directorio donde se encuentra el script principal
        current_dir = os.path.dirname(os.path.abspath(__file__))

        if platform.system() == "Windows":
            # Para Windows, usar 'start' para crear nueva ventana
            create_new_console = subprocess.CREATE_NEW_CONSOLE
            process = subprocess.Popen(
                cmd,
                creationflags=create_new_console,
                stdout=open(log_file, 'w'),
                stderr=subprocess.STDOUT,
                cwd=current_dir  # Establecer el directorio de trabajo al directorio del script
            )
        elif platform.system() == "Darwin":
            # Para macOS, usar osascript para abrir una nueva ventana en Terminal
            terminal_cmd = ["osascript", "-e", f'tell app "Terminal" to do script "{" ".join(cmd)}"']
            process = subprocess.Popen(
                terminal_cmd,
                stdout=open(log_file, 'w'),
                stderr=subprocess.STDOUT,
                cwd=current_dir  # Establecer el directorio de trabajo al directorio del script
            )
        else:
            # Para Linux, usar xterm
            terminal_cmd = ['xterm', '-title', process_name, '-e']
            full_cmd = terminal_cmd + [' '.join(cmd)]
            process = subprocess.Popen(
                full_cmd,
                stdout=open(log_file, 'w'),
                stderr=subprocess.STDOUT,
                cwd=current_dir  # Establecer el directorio de trabajo al directorio del script
            )

        return process, log_file

    def launch_taxi(self, taxi_data: Dict, taxi_id: int):
        """Lanza un proceso de taxi con los datos proporcionados"""
        # Obtener la ruta absoluta de taxi.py en el directorio actual
        current_dir = os.path.dirname(os.path.abspath(__file__))
        taxi_script_path = os.path.join(current_dir, 'taxi.py')

        cmd = [
            sys.executable, taxi_script_path,  # Usar la ruta absoluta del archivo taxi.py
            str(taxi_data['N']),
            str(taxi_data['M']),
            str(taxi_data['pos_x']),
            str(taxi_data['pos_y']),
            str(taxi_data['velocidad']),
            str(taxi_data['num_servicios'])
        ]
        process, log_file = self.create_process_window(cmd, f"taxi_{taxi_id}")
        return process, log_file

    def launch_user(self, user_data: Dict, user_id: int):
        """Lanza un proceso de usuario con los datos proporcionados"""
        # Obtener la ruta absoluta de usuario.py en el directorio actual
        current_dir = os.path.dirname(os.path.abspath(__file__))
        user_script_path = os.path.join(current_dir, 'usuario.py')

        cmd = [
            sys.executable, user_script_path,  # Usar la ruta absoluta del archivo usuario.py
            str(user_data['pos_x']),
            str(user_data['pos_y'])
        ]
        process, log_file = self.create_process_window(cmd, f"user_{user_id}")
        return process, log_file

    def monitor_process(self, process: subprocess.Popen, log_file: str, process_name: str):
        """Monitorea el estado de un proceso y su archivo de log"""
        while process.poll() is None:
            with open(log_file, 'r') as f:
                latest_logs = f.readlines()
                if latest_logs:
                    print(f"{process_name}: {latest_logs[-1].strip()}")
            time.sleep(1)

    def launch_nodes(self, num_taxis: int, num_users: int):
        """Lanza el número especificado de taxis y usuarios"""
        processes = []
        process_info = []

        # Lanzar taxis
        print(f"\n🚕 Lanzando {num_taxis} taxis...")
        for i in range(num_taxis):
            taxi_data = self.generate_taxi_data()
            process, log_file = self.launch_taxi(taxi_data, i + 1)
            processes.append(process)
            process_info.append({
                'process': process,
                'log_file': log_file,
                'name': f"Taxi {i + 1}"
            })
            print(f"✅ Taxi {i + 1}/{num_taxis} lanzado en posición ({taxi_data['pos_x']}, {taxi_data['pos_y']})")
            time.sleep(0.5)

        # Lanzar usuarios
        print(f"\n👤 Lanzando {num_users} usuarios...")
        for i in range(num_users):
            user_data = self.generate_user_data()
            process, log_file = self.launch_user(user_data, i + 1)
            processes.append(process)
            process_info.append({
                'process': process,
                'log_file': log_file,
                'name': f"Usuario {i + 1}"
            })
            print(f"✅ Usuario {i + 1}/{num_users} lanzado en posición ({user_data['pos_x']}, {user_data['pos_y']})")
            time.sleep(0.5)

        print("\n📊 Monitoreando procesos...")
        try:
            while any(p['process'].poll() is None for p in process_info):
                for info in process_info:
                    if info['process'].poll() is None:  # Si el proceso sigue activo
                        try:
                            with open(info['log_file'], 'r') as f:
                                latest_logs = f.readlines()
                                if latest_logs:
                                    print(f"{info['name']}: {latest_logs[-1].strip()}")
                        except Exception as e:
                            print(f"Error leyendo logs de {info['name']}: {e}")
                time.sleep(1)
        except KeyboardInterrupt:
            print("\n🛑 Deteniendo todos los procesos...")
            for p in processes:
                p.terminate()
            print("✅ Todos los procesos han sido detenidos.")

        return processes


def main():
    print("=== 🚀 Lanzador de Múltiples Nodos 🚀 ===")
    print("Los logs se guardarán en el directorio 'process_logs'")

    grid_n = int(input("📏 Ingrese el tamaño N de la cuadrícula: "))
    grid_m = int(input("📏 Ingrese el tamaño M de la cuadrícula: "))
    num_taxis = int(input("🚕 Ingrese el número de taxis a lanzar: "))
    num_users = int(input("👤 Ingrese el número de usuarios a lanzar: "))

    launcher = MultiNodeLauncher(grid_n, grid_m)

    try:
        processes = launcher.launch_nodes(num_taxis, num_users)
        print("\n✨ Todos los procesos han finalizado.")
    except KeyboardInterrupt:
        print("\n🛑 Programa terminado por el usuario.")
    except Exception as e:
        print(f"\n❌ Error: {e}")
    finally:
        print("👋 ¡Hasta luego!")


if __name__ == "__main__":
    main()
