# Proyecto My-Uber

## Descripción General
Este proyecto implementa un sistema distribuido similar a Uber, diseñado para simular taxis recorriendo una ciudad y usuarios solicitando servicios. El sistema consta de un servidor central que registra las posiciones de los taxis, recibe solicitudes de los usuarios y asigna taxis a los servicios.

## Requisitos del Sistema
- Python 3.7+
- gRPC
- Protocol Buffers

## Estructura del Proyecto
- `server.py`: Implementación del servidor central
- `taxi.py`: Implementación de la lógica de los taxis
- `client.py`: Implementación básica del cliente (para futuras expansiones)
- `my_uber_pb2.py`: Definiciones de Protocol Buffers generadas
- `my_uber_pb2_grpc.py`: Código gRPC generado

## Instalación

1. Clonar el repositorio:
   ```
   git clone https://github.com/Nidhood/Proyecto-Distribuidos.git
   cd Proyecto-Distribuidos
   ```

2. Crear y activar un entorno virtual:
   ```
   python -m venv venv
   source venv/bin/activate  # En Windows: venv\Scripts\activate
   ```

3. Instalar las dependencias:
   ```
   pip install grpcio grpcio-tools
   ```

4. Generar los archivos de Protocol Buffers y gRPC (si no están incluidos):
   ```
   python -m grpc_tools.protoc -I. --python_out=. --grpc_python_out=. my_uber.proto
   ```


## Uso

1. Iniciar el servidor:
   ```
   python server.py
   ```

2. Iniciar uno o más taxis (en terminales separadas):
   ```
   python taxi.py <taxi_id> <grid_n> <grid_m> <initial_x> <initial_y> <speed>
   ```
   Ejemplo:
   ```
   python taxi.py 1 10 10 5 5 2
   ```

3. (Funcionalidad futura) Iniciar el cliente para simular solicitudes de usuarios:
   ```
   python client.py
   ```

## Funcionalidades Implementadas
- Registro de taxis en el servidor central
- Actualización periódica de la posición de los taxis
- Asignación aleatoria de servicios a taxis disponibles
- Comunicación entre componentes utilizando gRPC

## Próximos Pasos
- Implementar la lógica completa de los usuarios
- Desarrollar el algoritmo para asignar el taxi más cercano a cada solicitud
- Agregar persistencia de datos (base de datos o archivo)
- Implementar tolerancia a fallos con un servidor de respaldo
- Mejorar la interfaz de usuario y la visualización del sistema

## Contribución
Las contribuciones son bienvenidas. Por favor, abra un issue para discutir cambios importantes antes de enviar un pull request.

## Licencia
Este proyecto está bajo la Licencia MIT. Consulte el archivo `LICENSE` para más detalles.