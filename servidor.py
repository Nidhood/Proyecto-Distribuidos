import time
from concurrent import futures
from datetime import datetime
import json
import logging

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

        # Configuración ZeroMQ para comunicación con taxis y clientes
        self.context = zmq.Context()

        # Socket publicador para enviar asignaciones a taxis
        self.publisher = self.context.socket(zmq.PUB)
        self.publisher.bind("tcp://*:5555")

        # Socket suscriptor para recibir solicitudes de clientes
        self.subscriber = self.context.socket(zmq.SUB)
        self.subscriber.connect("tcp://localhost:5559")  # Se conecta al broker
        self.subscriber.setsockopt_string(zmq.SUBSCRIBE, "solicitud_servicio")

        # Iniciar procesamiento de mensajes
        self.active = True
        self.start_message_processing()

    def start_message_processing(self):
        """Inicia un thread para procesar mensajes ZeroMQ"""
        import threading
        self.message_thread = threading.Thread(target=self.process_zmq_messages)
        self.message_thread.daemon = True
        self.message_thread.start()

    def process_zmq_messages(self):
        """Procesa mensajes entrantes de ZeroMQ"""
        while self.active:
            try:
                topic, message = self.subscriber.recv_string().split(" ", 1)
                data = json.loads(message)

                if topic == "solicitud_servicio":
                    self.handle_service_request(data)
            except Exception as e:
                logging.error(f"Error processing ZMQ message: {e}")

    def handle_service_request(self, data):
        """Maneja las solicitudes de servicio de los clientes"""
        try:
            # Crear solicitud de servicio para el gestor de BD
            request = taxi_service_pb2.CreateServiceRequest(
                client_id=data['id_cliente'],
                client_position=taxi_service_pb2.Position(
                    latitude=data['posicion']['lat'],
                    longitude=data['posicion']['lng'],
                    timestamp=str(datetime.fromtimestamp(data['timestamp']))
                )
            )

            # Solicitar servicio al gestor de BD
            response = self.db_stub.CreateService(request)

            if response.success:
                # Publicar asignación al taxi
                assign_message = {
                    'tipo': 'asignacion_servicio',
                    'id_taxi': response.assigned_taxi_id,
                    'id_servicio': response.service_id,
                    'posicion_cliente': data['posicion']
                }
                self.publisher.send_string(f"asignacion_taxi {json.dumps(assign_message)}")

                # Publicar confirmación al cliente
                confirm_message = {
                    'tipo': 'confirmacion_servicio',
                    'id_cliente': data['id_cliente'],
                    'id_taxi': response.assigned_taxi_id,
                    'id_servicio': response.service_id
                }
                self.publisher.send_string(f"resultado_servicio {json.dumps(confirm_message)}")
            else:
                # Notificar al cliente que no hay taxis disponibles
                error_message = {
                    'tipo': 'error',
                    'id_cliente': data['id_cliente'],
                    'mensaje': 'No hay taxis disponibles'
                }
                self.publisher.send_string(f"resultado_servicio {json.dumps(error_message)}")

        except Exception as e:
            logging.error(f"Error handling service request: {e}")

    def HealthCheck(self, request, context):
        """Implementación del método HealthCheck del proto"""
        try:
            response = self.db_stub.HealthCheck(request)
            return response
        except Exception as e:
            return taxi_service_pb2.HealthCheckResponse(
                status=False,
                message=str(e)
            )

    def RegisterTaxi(self, request, context):
        """Implementación del método RegisterTaxi del proto"""
        try:
            response = self.db_stub.RegisterTaxi(request)
            if response.success:
                # Publicar registro exitoso
                register_message = {
                    'tipo': 'registro_taxi',
                    'id_taxi': request.taxi_id,
                    'mensaje': 'Registro exitoso'
                }
                self.publisher.send_string(f"registro_taxi {json.dumps(register_message)}")
            return response
        except Exception as e:
            return taxi_service_pb2.RegisterTaxiResponse(
                success=False,
                message=str(e)
            )

    def UpdateTaxiPosition(self, request, context):
        """Implementación del método UpdateTaxiPosition del proto"""
        try:
            response = self.db_stub.UpdateTaxiPosition(request)
            if response.success:
                # Publicar actualización de posición
                position_message = {
                    'tipo': 'actualizacion_posicion',
                    'id_taxi': request.taxi_id,
                    'posicion': {
                        'lat': request.position.latitude,
                        'lng': request.position.longitude
                    },
                    'estado': request.status
                }
                self.publisher.send_string(f"posicion_taxi {json.dumps(position_message)}")
            return response
        except Exception as e:
            return taxi_service_pb2.UpdateTaxiPositionResponse(
                success=False,
                message=str(e)
            )

    def GetStatistics(self, request, context):
        """Implementación del método GetStatistics del proto"""
        try:
            response = self.db_stub.GetStatistics(request)
            return response
        except Exception as e:
            context.abort(grpc.StatusCode.INTERNAL, str(e))


def serve():
    """Inicia el servidor gRPC"""
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=10))
    taxi_service_pb2_grpc.add_TaxiDatabaseServiceServicer_to_server(
        TaxiServer(), server)
    server.add_insecure_port('[::]:50051')
    server.start()
    print("Server started on port 50051")
    try:
        while True:
            time.sleep(86400)
    except KeyboardInterrupt:
        server.stop(0)


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    serve()

