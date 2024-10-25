import time
from concurrent import futures
from datetime import datetime
import json
import logging
import math
import uuid

import grpc
import zmq

import taxi_service_pb2
import taxi_service_pb2_grpc


class TaxiServer(taxi_service_pb2_grpc.TaxiDatabaseServiceServicer):
    def __init__(self, db_service_address='localhost:50052'):
        # Configuración gRPC para comunicación con gestor_db
        self.message_thread = None
        self.db_channel = grpc.insecure_channel(db_service_address)
        self.db_stub = taxi_service_pb2_grpc.TaxiDatabaseServiceStub(self.db_channel)

        # Configuración ZeroMQ
        self.context = zmq.Context()

        # Socket suscriptor para recibir mensajes del broker
        self.subscriber = self.context.socket(zmq.SUB)
        self.subscriber.connect("tcp://localhost:5556")  # Conectar al backend del broker

        # Socket publicador para enviar mensajes al broker
        self.publisher = self.context.socket(zmq.PUB)
        self.publisher.connect("tcp://localhost:5559")  # Conectar al frontend del broker

        # Suscribirse a todos los tipos de mensajes necesarios
        for topic in ["solicitud_servicio", "registro_taxi", "posicion_taxi"]:
            self.subscriber.setsockopt_string(zmq.SUBSCRIBE, topic)

        # Estado interno
        self.taxis = {}  # Almacena información de taxis: {id_taxi: {posicion, estado, servicios_realizados}}
        self.servicios_activos = {}  # Almacena servicios en curso

        # Iniciar procesamiento de mensajes
        self.active = True
        self.start_message_processing()

    def calcular_distancia(self, pos1, pos2):
        """Calcula la distancia euclidiana entre dos posiciones"""
        return math.sqrt(
            (pos1['lat'] - pos2['lat']) ** 2 +
            (pos1['lng'] - pos2['lng']) ** 2
        )

    def encontrar_taxi_mas_cercano(self, posicion_cliente):
        """Encuentra el taxi disponible más cercano"""
        taxis_disponibles = [
            (id_taxi, info) for id_taxi, info in self.taxis.items()
            if info['estado'] == 'disponible'
        ]

        if not taxis_disponibles:
            return None

        # Ordenar por distancia y luego por ID en caso de empate
        taxis_ordenados = sorted(
            taxis_disponibles,
            key=lambda x: (
                self.calcular_distancia(x[1]['posicion'], posicion_cliente),
                x[0]  # ID del taxi como segundo criterio
            )
        )

        return taxis_ordenados[0][0]  # Retorna el ID del taxi más cercano

    def start_message_processing(self):
        import threading
        self.message_thread = threading.Thread(target=self.process_zmq_messages)
        self.message_thread.daemon = True
        self.message_thread.start()

    def process_zmq_messages(self):
        while self.active:
            try:
                message = self.subscriber.recv_string()
                if " " in message:
                    topic, message_data = message.split(" ", 1)
                    data = json.loads(message_data)

                    if topic == "solicitud_servicio":
                        self.handle_service_request(data)
                    elif topic == "registro_taxi":
                        self.handle_taxi_registration(data)
                    elif topic == "posicion_taxi":
                        self.handle_taxi_position_update(data)
                else:
                    logging.error(f"Mensaje ZMQ mal formateado: {message}")
            except Exception as e:
                logging.error(f"Error al procesar mensaje ZMQ: {e}")

    def handle_taxi_registration(self, data):
        try:
            request = taxi_service_pb2.RegisterTaxiRequest(
                taxi_id=data['id_taxi'],
                initial_position=taxi_service_pb2.Position(
                    latitude=data['posicion']['lat'],
                    longitude=data['posicion']['lng'],
                    timestamp=str(datetime.fromtimestamp(data['timestamp']))
                )
            )
            response = self.db_stub.RegisterTaxi(request)

            if response.success:
                self.taxis[data['id_taxi']] = {
                    'posicion': data['posicion'],
                    'estado': data['estado'],
                    'servicios_realizados': 0,
                    'posicion_inicial': data['posicion'].copy()
                }
                logging.info(f"Taxi {data['id_taxi']} registrado exitosamente")
            else:
                logging.error(f"Error al registrar taxi: {response.message}")

        except Exception as e:
            logging.error(f"Error en registro de taxi: {e}")

    def handle_taxi_position_update(self, data):
        try:
            request = taxi_service_pb2.UpdateTaxiPositionRequest(
                taxi_id=data['id_taxi'],
                position=taxi_service_pb2.Position(
                    latitude=data['posicion']['lat'],
                    longitude=data['posicion']['lng'],
                    timestamp=str(datetime.fromtimestamp(data['timestamp']))
                ),
                status=data['estado']
            )
            response = self.db_stub.UpdateTaxiPosition(request)

            if response.success:
                if data['id_taxi'] in self.taxis:
                    self.taxis[data['id_taxi']].update({
                        'posicion': data['posicion'],
                        'estado': data['estado']
                    })
                    logging.info(f"Posición actualizada para taxi {data['id_taxi']}")
            else:
                logging.error(f"Error al actualizar posición: {response.message}")

        except Exception as e:
            logging.error(f"Error en actualización de posición: {e}")

    def handle_service_request(self, data):
        try:
            # Buscar taxi más cercano
            taxi_id = self.encontrar_taxi_mas_cercano(data['posicion'])

            if not taxi_id:
                # No hay taxis disponibles
                error_message = {
                    'tipo': 'resultado_servicio',
                    'subtipo': 'error',
                    'id_cliente': data['id_cliente'],
                    'mensaje': 'No hay taxis disponibles'
                }
                self.publisher.send_string(f"resultado_servicio {json.dumps(error_message)}")
                return

            # Crear servicio en la base de datos
            service_id = str(uuid.uuid4())
            request = taxi_service_pb2.CreateServiceRequest(
                client_id=data['id_cliente'],
                client_position=taxi_service_pb2.Position(
                    latitude=data['posicion']['lat'],
                    longitude=data['posicion']['lng'],
                    timestamp=str(datetime.fromtimestamp(data['timestamp']))
                )
            )
            response = self.db_stub.CreateService(request)

            if response.success:
                # Actualizar estado del taxi
                self.taxis[taxi_id]['estado'] = 'ocupado'
                self.servicios_activos[service_id] = {
                    'taxi_id': taxi_id,
                    'client_id': data['id_cliente'],
                    'start_time': time.time()
                }

                # Notificar al taxi
                assign_message = {
                    'tipo': 'asignacion_servicio',
                    'id_taxi': taxi_id,
                    'id_servicio': service_id,
                    'posicion_cliente': data['posicion']
                }
                self.publisher.send_string(f"asignacion_taxi {json.dumps(assign_message)}")

                # Notificar al cliente
                confirm_message = {
                    'tipo': 'resultado_servicio',
                    'subtipo': 'confirmacion_servicio',
                    'id_cliente': data['id_cliente'],
                    'id_servicio': service_id,
                    'id_taxi': taxi_id,
                    'posicion_taxi': self.taxis[taxi_id]['posicion']
                }
                self.publisher.send_string(f"resultado_servicio {json.dumps(confirm_message)}")

                # Incrementar contador de servicios
                self.taxis[taxi_id]['servicios_realizados'] += 1
                logging.info(f"Servicio {service_id} asignado al taxi {taxi_id}")

            else:
                error_message = {
                    'tipo': 'resultado_servicio',
                    'subtipo': 'error',
                    'id_cliente': data['id_cliente'],
                    'mensaje': 'Error al crear el servicio'
                }
                self.publisher.send_string(f"resultado_servicio {json.dumps(error_message)}")

        except Exception as e:
            logging.error(f"Error al manejar solicitud de servicio: {e}")
            error_message = {
                'tipo': 'resultado_servicio',
                'subtipo': 'error',
                'id_cliente': data['id_cliente'],
                'mensaje': f'Error interno: {str(e)}'
            }
            self.publisher.send_string(f"resultado_servicio {json.dumps(error_message)}")

    def cleanup_completed_services(self):
        """Limpia los servicios completados y restaura el estado de los taxis"""
        current_time = time.time()
        completed_services = []

        for service_id, service_info in self.servicios_activos.items():
            if current_time - service_info['start_time'] >= 30:  # 30 segundos de servicio
                taxi_id = service_info['taxi_id']
                if taxi_id in self.taxis:
                    # Restaurar posición inicial si no ha completado todos los servicios
                    self.taxis[taxi_id]['posicion'] = self.taxis[taxi_id]['posicion_inicial'].copy()
                    self.taxis[taxi_id]['estado'] = 'disponible'
                    completed_services.append(service_id)

        # Eliminar servicios completados
        for service_id in completed_services:
            del self.servicios_activos[service_id]

    def serve(self):
        """Inicia el servidor"""
        server = grpc.server(futures.ThreadPoolExecutor(max_workers=10))
        taxi_service_pb2_grpc.add_TaxiDatabaseServiceServicer_to_server(self, server)
        server.add_insecure_port('[::]:50051')
        server.start()
        logging.info("Servidor iniciado en puerto 50051")

        try:
            while True:
                self.cleanup_completed_services()
                time.sleep(1)
        except KeyboardInterrupt:
            self.active = False
            self.message_thread.join()
            server.stop(0)
            logging.info("Servidor detenido")


def main():
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s'
    )
    server = TaxiServer()
    server.serve()


if __name__ == "__main__":
    main()
