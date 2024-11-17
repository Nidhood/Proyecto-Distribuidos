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
    def __init__(self, db_service_address='localhost:50052', secondary_address='localhost:50054', is_primary=True):

        # Configuración de logging
        self.logger = logging.getLogger(f'TaxiServer-{"Primary" if is_primary else "Secondary"}')
        if not self.logger.handlers:
            self.logger.setLevel(logging.INFO)
            formatter = logging.Formatter('🚦 %(asctime)s - %(message)s')
            ch = logging.StreamHandler()
            ch.setFormatter(formatter)
            self.logger.addHandler(ch)
        self.logger.propagate = False
        self.is_paused = not is_primary
        self.active = True

        # Configuración gRPC para comunicación con gestor_db
        self.message_thread = None
        self.db_channel = grpc.insecure_channel(db_service_address)
        self.db_stub = taxi_service_pb2_grpc.TaxiDatabaseServiceStub(self.db_channel)

        # Configuración ZeroMQ
        self.context = zmq.Context()
        self.subscriber = self.context.socket(zmq.SUB)
        self.subscriber.connect("tcp://localhost:5558")
        self.publisher = self.context.socket(zmq.PUB)
        self.publisher.connect("tcp://localhost:5557")

        # Suscribirse a todos los tipos de mensajes necesarios
        for topic in ["solicitud_servicio", "registro_taxi", "posicion_taxi"]:
            self.subscriber.setsockopt_string(zmq.SUBSCRIBE, topic)

        # Estado interno
        self.taxis = {}  # {id_taxi: {'posicion': {}, 'estado': str, 'servicios_realizados': int, 'ultimo_update': float}}
        self.servicios_activos = {}  # {service_id: {'taxi_id': str, 'client_id': str, 'start_time': float}}
        self.registered_taxis = set()

        self.is_primary = is_primary
        self.secondary_address = secondary_address
        self.active = True

        # Iniciar procesamiento de mensajes
        self.start_message_processing()
        self.logger.info(f"{'Servidor primario' if is_primary else 'Servidor secundario'} iniciado")

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
               current_time - info.get('ultimo_update', 0) < 60  # Solo taxis activos
        ]

        if not taxis_disponibles:
            self.logger.warning("❌ No hay taxis disponibles en este momento")
            return None

        # Calcular distancias y ordenar por cercanía
        taxis_con_distancia = []
        for id_taxi, info in taxis_disponibles:
            distancia = self.calcular_distancia(info['posicion'], posicion_cliente)
            taxis_con_distancia.append((id_taxi, info, distancia))

        # Ordenar por distancia y luego por ID en caso de empate
        taxis_ordenados = sorted(
            taxis_con_distancia,
            key=lambda x: (x[2], x[0])
        )

        taxi_elegido = taxis_ordenados[0][0]
        self.logger.info(f"✅ Taxi {taxi_elegido} asignado a cliente en posición {posicion_cliente}")
        return taxi_elegido

    def start_message_processing(self):
        """Inicia el procesamiento de mensajes en un thread separado"""
        self.message_thread = threading.Thread(target=self.process_zmq_messages)
        self.message_thread.daemon = True
        self.message_thread.start()
        time.sleep(1)  # Dar tiempo para conexiones

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
                            elif topic == "servicio_completado":
                                self.handle_service_completion(data)
                else:
                    time.sleep(1)  # Esperar mientras está pausado
            except Exception as e:
                self.logger.error(f"Error al procesar mensaje ZMQ: {e}")
                if not self.active:
                    break

    def handle_service_completion(self, data):
        """Maneja la finalización de un servicio"""
        try:
            service_id = data['id_servicio']
            if service_id in self.servicios_activos:
                # Actualizar estado en la base de datos
                request = taxi_service_pb2.UpdateServiceRequest(
                    service_id=service_id,
                    status='COMPLETED',
                    completion_timestamp=str(datetime.fromtimestamp(data['timestamp']))
                )
                response = self.db_stub.UpdateService(request)

                if response.success:
                    # Notificar al cliente
                    client_id = self.servicios_activos[service_id]['client_id']
                    completion_message = {
                        'tipo': 'resultado_servicio',
                        'subtipo': 'servicio_completado',
                        'id_cliente': client_id,
                        'id_servicio': service_id,
                        'id_taxi': data['id_taxi'],
                        'timestamp': data['timestamp'],
                        'estado': 'COMPLETADO'
                    }
                    self.publisher.send_string(f"resultado_servicio {json.dumps(completion_message)}")

                    # Actualizar estado del taxi
                    if data['id_taxi'] in self.taxis:
                        self.taxis[data['id_taxi']]['estado'] = 'AVAILABLE'
                        self.taxis[data['id_taxi']]['posicion'] = data.get('posicion_final',
                                                                           self.taxis[data['id_taxi']]['posicion'])

                    # Limpiar el servicio de la lista de activos
                    del self.servicios_activos[service_id]
                    self.logger.info(f"✅ Servicio {service_id} completado correctamente")
                else:
                    self.logger.error(f"❌ Error al actualizar servicio en la base de datos: {response.message}")

            else:
                self.logger.warning(f"⚠️ Servicio {service_id} no encontrado en servicios activos")

        except Exception as e:
            self.logger.error(f"❌ Error al manejar completación de servicio: {e}")

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

    def handle_service_request(self, data):
        """Maneja las solicitudes de servicio de los usuarios"""
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
                self.publisher.send_string(f"resultado_servicio {json.dumps(error_message)}")
                self.logger.info(f"❌ No hay taxis disponibles para el cliente {data['id_cliente']}")
                return

            # Crear servicio en la base de datos
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
                # Actualizar estado del taxi
                self.taxis[taxi_id]['estado'] = 'BUSY'
                self.servicios_activos[service_id] = {
                    'taxi_id': taxi_id,
                    'client_id': data['id_cliente'],
                    'start_time': time.time(),
                    'status': 'ASSIGNED'
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

                self.taxis[taxi_id]['servicios_realizados'] += 1
                self.logger.info(f"✅ Servicio {service_id} asignado al taxi {taxi_id}")

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
        """Maneja el registro de nuevos taxis"""
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
                    self.logger.info(f"✅ Taxi {data['id_taxi']} registrado exitosamente")

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
            else:
                self.logger.info(f"ℹ️ Taxi {data['id_taxi']} ya está registrado")

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
        """Maneja las actualizaciones de posición de los taxis"""
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
                        self.logger.info(f"📍 Posición actualizada para taxi {data['id_taxi']}")
                else:
                    self.logger.error(f"❌ Error al actualizar posición: {response.message}")

        except Exception as e:
            self.logger.error(f"❌ Error en actualización de posición: {e}")

    def cleanup_completed_services(self):
        """Limpia los servicios completados y restaura el estado de los taxis"""
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
            self.logger.info(f"✅ Servicio {service_id} completado y taxi liberado")

    def replicate_state(self):
        """Replica el estado al servidor secundario"""
        if not self.is_primary:
            return

        time.sleep(4)
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
                    self.logger.info("🔄 Estado replicado al servidor secundario")
            except Exception as e:
                self.logger.error(f"❌ Error replicando estado a {self.secondary_address}: {e}")

    def ReplicateState(self, request, context):
        """Recibe la replicación del estado"""
        if self.is_primary:
            return taxi_service_pb2.ReplicateStateResponse(success=False)

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

    def HealthCheck(self, request, context):
        """Verifica el estado de salud del servidor"""
        try:
            return taxi_service_pb2.HealthCheckResponse(
                status=True,
                message="Server is healthy",
                timestamp=datetime.now().isoformat()
            )
        except Exception as e:
            return taxi_service_pb2.HealthCheckResponse(
                status=False,
                message=f"Health check failed: {str(e)}",
                timestamp=datetime.now().isoformat()
            )

    def pause_server(self):
        """Pausa el procesamiento de mensajes del servidor"""
        self.is_paused = True
        self.logger.info("Servidor pausado")

    def resume_server(self):
        """Reanuda el procesamiento de mensajes del servidor"""
        self.is_paused = False
        self.logger.info("Servidor reanudado")

    def run(self, port=50051):
        """Ejecuta el servidor en un bucle principal"""
        server = grpc.server(futures.ThreadPoolExecutor(max_workers=10))
        taxi_service_pb2_grpc.add_TaxiDatabaseServiceServicer_to_server(self, server)
        server.add_insecure_port(f'[::]:{port}')
        server.start()
        self.logger.info(f"🚀 {'Servidor primario' if self.is_primary else 'Servidor secundario'} iniciado en puerto {port}")

        try:
            while True:
                time.sleep(1)
                self.cleanup_completed_services()
                if self.is_primary:
                    self.replicate_state()
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
    secondary_address = 'localhost:50054'
    server = TaxiServer(is_primary=True, secondary_address=secondary_address)
    try:
        server.run()
    except KeyboardInterrupt:
        print("\n🛑 Servidor detenido por el usuario")
    except Exception as e:
        print(f"❌ Error: {e}")


if __name__ == "__main__":
    main()
