import json
import logging
import math
import threading
import time
import uuid
from datetime import datetime
from concurrent import futures

import grpc
import zmq

import taxi_service_pb2
import taxi_service_pb2_grpc

class TaxiServer(taxi_service_pb2_grpc.TaxiDatabaseServiceServicer):
    def __init__(self, db_service_address='localhost:50052', is_primary=False, secondary_address=None):
        self.message_thread = None
        self.db_channel = grpc.insecure_channel(db_service_address)
        self.db_stub = taxi_service_pb2_grpc.TaxiDatabaseServiceStub(self.db_channel)

        self.context = zmq.Context()
        self.subscriber = self.context.socket(zmq.SUB)
        self.subscriber.connect("tcp://localhost:5558")
        self.publisher = self.context.socket(zmq.PUB)
        self.publisher.connect("tcp://localhost:5557")

        for topic in ["solicitud_servicio", "registro_taxi", "posicion_taxi"]:
            self.subscriber.setsockopt_string(zmq.SUBSCRIBE, topic)

        self.taxis = {}
        self.servicios_activos = {}
        self.registered_taxis = set()

        self.is_primary = is_primary
        self.secondary_address = secondary_address

        self.active = True
        self.start_message_processing()

    def start_message_processing(self):
        """Inicia el procesamiento de mensajes en un thread separado"""
        self.message_thread = threading.Thread(target=self.process_zmq_messages)
        self.message_thread.daemon = True
        self.message_thread.start()

        # Dar tiempo para que se establezcan las conexiones
        time.sleep(1)

    def process_zmq_messages(self):
        poller = zmq.Poller()
        poller.register(self.subscriber, zmq.POLLIN)

        while self.active:
            try:
                socks = dict(poller.poll(timeout=1000))
                if self.subscriber in socks:
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
            except Exception as e:
                logging.error(f"Error al procesar mensaje ZMQ: {e}")
                if not self.active:
                    break

    def handle_taxi_registration(self, data):
        try:
            # Solo registrar si el taxi no está ya registrado
            if data['id_taxi'] not in self.registered_taxis:
                request = taxi_service_pb2.RegisterTaxiRequest(
                    taxi_id=data['id_taxi'],
                    initial_position=taxi_service_pb2.Position(
                        latitude=data['posicion']['lat'],
                        longitude=data['posicion']['lng'],
                        timestamp=str(datetime.fromtimestamp(data['timestamp']))
                    ),
                    status='AVAILABLE'  # Agregamos el estado inicial
                )
                response = self.db_stub.RegisterTaxi(request)

                if response.success:
                    self.registered_taxis.add(data['id_taxi'])  # Marcar como registrado
                    self.taxis[data['id_taxi']] = {
                        'posicion': data['posicion'],
                        'estado': data['estado'],
                        'servicios_realizados': 0,
                        'posicion_inicial': data['posicion'].copy()
                    }
                    logging.info(f"Taxi {data['id_taxi']} registrado exitosamente")

                    # Confirmación al taxi
                    confirm_message = {
                        'tipo': 'confirmacion_registro',
                        'id_taxi': data['id_taxi'],
                        'estado': 'success'
                    }
                    self.publisher.send_string(f"confirmacion_taxi {json.dumps(confirm_message)}")
                else:
                    logging.error(f"Error al registrar taxi: {response.message}")
                    # Notificar error al taxi
                    error_message = {
                        'tipo': 'confirmacion_registro',
                        'id_taxi': data['id_taxi'],
                        'estado': 'error',
                        'mensaje': response.message
                    }
                    self.publisher.send_string(f"confirmacion_taxi {json.dumps(error_message)}")
            else:
                logging.info(f"Taxi {data['id_taxi']} ya está registrado")

        except Exception as e:
            logging.error(f"Error en registro de taxi: {e}")
            # Notificar error al taxi
            error_message = {
                'tipo': 'confirmacion_registro',
                'id_taxi': data['id_taxi'],
                'estado': 'error',
                'mensaje': str(e)
            }
            self.publisher.send_string(f"confirmacion_taxi {json.dumps(error_message)}")

    def handle_taxi_position_update(self, data):
        try:
            # Verificar si el taxi está registrado
            if data['id_taxi'] not in self.registered_taxis:
                # Si no está registrado, intentar registrarlo primero
                self.handle_taxi_registration(data)
                return  # Retornar para evitar actualizar la posición inmediatamente

            # Proceder con la actualización de posición solo si el taxi está registrado
            if data['id_taxi'] in self.registered_taxis:
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
                error_message = {
                    'tipo': 'resultado_servicio',
                    'subtipo': 'error',
                    'id_cliente': data['id_cliente'],
                    'mensaje': 'No hay taxis disponibles'
                }
                # Modificar el topic para que coincida con la suscripción del usuario
                self.publisher.send_string(f"resultado_servicio {json.dumps(error_message)}")
                logging.info(f"No hay taxis disponibles para el cliente {data['id_cliente']}")
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
            logging.info(f"Servicio {service_id} completado y taxi liberado")

    def replicate_state(self):
        state = {
            'taxis': self.taxis,
            'servicios_activos': self.servicios_activos,
            'registered_taxis': list(self.registered_taxis)
        }
        if self.secondary_address:
            try:
                with grpc.insecure_channel(self.secondary_address) as channel:
                    stub = taxi_service_pb2_grpc.TaxiDatabaseServiceStub(channel)
                    request = taxi_service_pb2.ReplicateStateRequest(state=json.dumps(state))
                    stub.ReplicateState(request)
            except Exception as e:
                logging.error(f"Error replicando estado a {self.secondary_address}: {e}")

    def ReplicateState(self, request, context):
        state = json.loads(request.state)
        self.taxis = state['taxis']
        self.servicios_activos = state['servicios_activos']
        self.registered_taxis = set(state['registered_taxis'])
        logging.info("Estado replicado recibido")
        return taxi_service_pb2.ReplicateStateResponse(success=True)

    def PromoteToPrimary(self, request, context):
        self.is_primary = True
        logging.info("Servidor promovido a primario")
        return taxi_service_pb2.PromoteToPrimaryResponse(success=True)

    def run(self, port=50054):
        server = grpc.server(futures.ThreadPoolExecutor(max_workers=10))
        taxi_service_pb2_grpc.add_TaxiDatabaseServiceServicer_to_server(self, server)
        server.add_insecure_port(f'[::]:{port}')
        server.start()
        logging.info(f'Servidor {"primario" if self.is_primary else "secundario"} iniciado en el puerto {port}')
        try:
            while True:
                time.sleep(1)
                self.cleanup_completed_services()
                if self.is_primary:
                    self.replicate_state()
        except KeyboardInterrupt:
            logging.info("Servidor detenido por el usuario.")
        finally:
            self.active = False
            self.message_thread.join()
            self.subscriber.close()
            self.publisher.close()
            self.context.term()
            self.db_channel.close()

def main():
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
    server = TaxiServer(is_primary=False)
    server.run()

if __name__ == "__main__":
    main()