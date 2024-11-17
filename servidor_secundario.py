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
    def __init__(self, db_service_address='localhost:50052', is_primary=False, secondary_address='localhost:50051'):
        # Configuración de logging
        self.logger = logging.getLogger(f'TaxiServer-{"Primary" if is_primary else "Secondary"}')
        if not self.logger.handlers:  # Evitar handlers duplicados
            self.logger.setLevel(logging.INFO)
            formatter = logging.Formatter('🚦 %(asctime)s - %(message)s')
            ch = logging.StreamHandler()
            ch.setFormatter(formatter)
            self.logger.addHandler(ch)
        self.is_paused = not is_primary
        self.active = True

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

        self.taxis = {}  # {id_taxi: {'posicion': {}, 'estado': str, 'servicios_realizados': int, 'ultimo_update': float}}
        self.servicios_activos = {}
        self.registered_taxis = set()

        self.is_primary = is_primary
        self.secondary_address = secondary_address

        self.active = True
        self.start_message_processing()
        self.logger.info("Servidor secundario iniciado")

    def calcular_distancia(self, pos1, pos2):
        """Calcula la distancia euclidiana entre dos posiciones"""
        return math.sqrt(
            (pos1['lat'] - pos2['lat']) ** 2 +
            (pos1['lng'] - pos2['lng']) ** 2
        )

    def encontrar_taxi_mas_cercano(self, posicion_cliente):
        """Encuentra el taxi disponible más cercano"""
        current_time = time.time()
        taxis_disponibles = [
            (id_taxi, info) for id_taxi, info in self.taxis.items()
            if info['estado'] == 'AVAILABLE' and
               current_time - info.get('ultimo_update', 0) < 60
        ]

        if not taxis_disponibles:
            self.logger.warning("❌ No hay taxis disponibles en este momento")
            return None

        taxis_con_distancia = []
        for id_taxi, info in taxis_disponibles:
            distancia = self.calcular_distancia(info['posicion'], posicion_cliente)
            taxis_con_distancia.append((id_taxi, info, distancia))

        taxis_ordenados = sorted(
            taxis_con_distancia,
            key=lambda x: (x[2], x[0])
        )

        taxi_elegido = taxis_ordenados[0][0]
        self.logger.info(f"✅ Taxi {taxi_elegido[:6]} asignado a cliente en posición {posicion_cliente}")
        return taxi_elegido

    def start_message_processing(self):
        self.message_thread = threading.Thread(target=self.process_zmq_messages)
        self.message_thread.daemon = True
        self.message_thread.start()
        time.sleep(1)

    def process_zmq_messages(self):
        """Procesa los mensajes ZMQ recibidos"""
        poller = zmq.Poller()
        poller.register(self.subscriber, zmq.POLLIN)

        while self.active:
            try:
                if not self.is_paused:  # Solo procesar mensajes si no está pausado
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
                else:
                    time.sleep(1)  # Esperar mientras está pausado
            except Exception as e:
                self.logger.error(f"Error al procesar mensaje ZMQ: {e}")
                if not self.active:
                    break

    def handle_service_request(self, data):
        try:
            taxi_id = self.encontrar_taxi_mas_cercano(data['posicion'])

            if not taxi_id:
                error_message = {
                    'tipo': 'resultado_servicio',
                    'subtipo': 'error',
                    'id_cliente': data['id_cliente'],
                    'mensaje': 'No hay taxis disponibles'
                }
                self.publisher.send_string(f"resultado_servicio {json.dumps(error_message)}")
                return

            service_id = str(uuid.uuid4())
            request = taxi_service_pb2.CreateServiceRequest(
                client_id=data['id_cliente'],
                client_position=taxi_service_pb2.Position(
                    latitude=data['posicion']['lat'],
                    longitude=data['posicion']['lng'],
                    timestamp=str(datetime.fromtimestamp(data['timestamp']))
                ),
                status='REQUESTED'
            )
            response = self.db_stub.CreateService(request)

            if response.success:
                self.taxis[taxi_id]['estado'] = 'BUSY'
                self.servicios_activos[service_id] = {
                    'taxi_id': taxi_id,
                    'client_id': data['id_cliente'],
                    'start_time': time.time()
                }

                assign_message = {
                    'tipo': 'asignacion_servicio',
                    'id_taxi': taxi_id,
                    'id_servicio': service_id,
                    'posicion_cliente': data['posicion']
                }
                self.publisher.send_string(f"asignacion_taxi {json.dumps(assign_message)}")

                confirm_message = {
                    'tipo': 'resultado_servicio',
                    'subtipo': 'confirmacion_servicio',
                    'id_cliente': data['id_cliente'],
                    'id_servicio': service_id,
                    'id_taxi': taxi_id,
                    'posicion_taxi': self.taxis[taxi_id]['posicion']
                }
                self.publisher.send_string(f"resultado_servicio {json.dumps(confirm_message)}")

                self.taxis[taxi_id]['servicios_realizados'] += 1
                self.logger.info(f"✅ Servicio {service_id[:6]} asignado al taxi {taxi_id[:6]}")

            else:
                error_message = {
                    'tipo': 'resultado_servicio',
                    'subtipo': 'error',
                    'id_cliente': data['id_cliente'],
                    'mensaje': 'Error al crear el servicio'
                }
                self.publisher.send_string(f"resultado_servicio {json.dumps(error_message)}")

        except Exception as e:
            self.logger.error(f"❌ Error al manejar solicitud de servicio: {e}")
            error_message = {
                'tipo': 'resultado_servicio',
                'subtipo': 'error',
                'id_cliente': data['id_cliente'],
                'mensaje': f'Error interno: {str(e)}'
            }
            self.publisher.send_string(f"resultado_servicio {json.dumps(error_message)}")

    def handle_taxi_registration(self, data):
        try:
            if data['id_taxi'] not in self.registered_taxis:
                request = taxi_service_pb2.RegisterTaxiRequest(
                    taxi_id=data['id_taxi'],
                    initial_position=taxi_service_pb2.Position(
                        latitude=data['posicion']['lat'],
                        longitude=data['posicion']['lng'],
                        timestamp=str(datetime.fromtimestamp(data['timestamp']))
                    ),
                    status='AVAILABLE'
                )
                response = self.db_stub.RegisterTaxi(request)

                if response.success:
                    self.registered_taxis.add(data['id_taxi'])
                    self.taxis[data['id_taxi']] = {
                        'posicion': data['posicion'],
                        'estado': data['estado'],
                        'servicios_realizados': 0,
                        'posicion_inicial': data['posicion'].copy(),
                        'ultimo_update': time.time()
                    }
                    self.logger.info(f"✅ Taxi {data['id_taxi'][:6]} registrado exitosamente")

                    confirm_message = {
                        'tipo': 'confirmacion_registro',
                        'id_taxi': data['id_taxi'],
                        'estado': 'success'
                    }
                    self.publisher.send_string(f"confirmacion_taxi {json.dumps(confirm_message)}")
                else:
                    self.logger.error(f"❌ Error al registrar taxi: {response.message}")
                    error_message = {
                        'tipo': 'confirmacion_registro',
                        'id_taxi': data['id_taxi'],
                        'estado': 'error',
                        'mensaje': response.message
                    }
                    self.publisher.send_string(f"confirmacion_taxi {json.dumps(error_message)}")

        except Exception as e:
            self.logger.error(f"❌ Error en registro de taxi: {e}")
            error_message = {
                'tipo': 'confirmacion_registro',
                'id_taxi': data['id_taxi'],
                'estado': 'error',
                'mensaje': str(e)
            }
            self.publisher.send_string(f"confirmacion_taxi {json.dumps(error_message)}")

    def handle_taxi_position_update(self, data):
        try:
            if data['id_taxi'] not in self.registered_taxis:
                self.handle_taxi_registration(data)
                return

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
                            'estado': data['estado'],
                            'ultimo_update': time.time()
                        })
                        self.logger.info(f"📍 Posición actualizada para taxi {data['id_taxi'][:6]}")
                else:
                    self.logger.error(f"❌ Error al actualizar posición: {response.message}")

        except Exception as e:
            self.logger.error(f"❌ Error en actualización de posición: {e}")

    def cleanup_completed_services(self):
        current_time = time.time()
        completed_services = []

        for service_id, service_info in self.servicios_activos.items():
            if current_time - service_info['start_time'] >= 30:
                taxi_id = service_info['taxi_id']
                if taxi_id in self.taxis:
                    self.taxis[taxi_id]['posicion'] = self.taxis[taxi_id]['posicion_inicial'].copy()
                    self.taxis[taxi_id]['estado'] = 'disponible'
                    completed_services.append(service_id)

        for service_id in completed_services:
            del self.servicios_activos[service_id]
            self.logger.info(f"✅ Servicio {service_id[:6]} completado y taxi liberado")

    def ReplicateState(self, request, context):
        try:
            state = json.loads(request.state)
            self.taxis = state['taxis']
            self.servicios_activos = state['servicios_activos']
            self.registered_taxis = set(state['registered_taxis'])
            self.logger.info("✅ Estado replicado recibido")
            return taxi_service_pb2.ReplicateStateResponse(success=True)
        except Exception as e:
            self.logger.error(f"❌ Error al recibir replicación: {e}")
            return taxi_service_pb2.ReplicateStateResponse(success=False)

    def pause_server(self):
        """Pausa el procesamiento de mensajes del servidor"""
        self.is_paused = True
        self.logger.info("Servidor pausado")

    def resume_server(self):
        """Reanuda el procesamiento de mensajes del servidor"""
        self.is_paused = False
        self.logger.info("Servidor reanudado")

    def PromoteToPrimary(self, request, context):
        """Promueve el servidor secundario a primario"""
        self.is_primary = True
        self.resume_server()  # Reanudar el servidor al ser promovido
        self.logger.info("🔄 Servidor promovido a primario")
        return taxi_service_pb2.PromoteToPrimaryResponse(success=True)

    def DemoteToSecondary(self, request, context):
        """Degrada el servidor a secundario"""
        self.is_primary = False
        self.pause_server()  # Pausar el servidor al ser degradado
        self.logger.info("🔄 Servidor degradado a secundario")
        return taxi_service_pb2.PromoteToPrimaryResponse(success=True)

    def run(self, port=50054):
        server = grpc.server(futures.ThreadPoolExecutor(max_workers=10))
        taxi_service_pb2_grpc.add_TaxiDatabaseServiceServicer_to_server(self, server)
        server.add_insecure_port(f'[::]:{port}')
        server.start()
        self.logger.info(f"🚀 Servidor secundario iniciado en puerto {port}")

        try:
            while True:
                time.sleep(1)
                self.cleanup_completed_services()
        except KeyboardInterrupt:
            self.logger.info("🛑 Servidor detenido por el usuario")
        finally:
            self.active = False
            if self.message_thread:
                self.message_thread.join()
            self.subscriber.close()
            self.publisher.close()
            self.context.term()
            self.db_channel.close()
            server.stop(0)

def main():
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
    server = TaxiServer(is_primary=False)
    try:
        server.run()
    except KeyboardInterrupt:
        print("\n🛑 Servidor detenido por el usuario")
    except Exception as e:
        print(f"❌ Error: {e}")

if __name__ == "__main__":
    main()