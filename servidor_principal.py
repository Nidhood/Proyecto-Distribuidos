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
from metricas import TaxiMetrics
from functools import wraps


class TaxiServer(taxi_service_pb2_grpc.TaxiDatabaseServiceServicer):
    def __init__(self, db_service_address='localhost:50052', secondary_address='localhost:50054'):
        # Configuración de logging
        self.logger = logging.getLogger(f'TaxiServer')
        if not self.logger.handlers:
            self.logger.setLevel(logging.INFO)
            formatter = logging.Formatter('🚦 %(asctime)s - %(message)s')
            ch = logging.StreamHandler()
            ch.setFormatter(formatter)
            self.logger.addHandler(ch)
        self.logger.propagate = False
        self.is_paused = True
        self.active = True

        self.message_thread = None
        self.db_channel = grpc.insecure_channel(db_service_address)
        self.db_stub = taxi_service_pb2_grpc.TaxiDatabaseServiceStub(self.db_channel)

        self.context = zmq.Context()
        self.subscriber = self.context.socket(zmq.SUB)
        self.subscriber.connect("tcp://localhost:5558")
        self.publisher = self.context.socket(zmq.PUB)
        self.publisher.connect("tcp://localhost:5557")
        self.metrics = TaxiMetrics(port=8000)

        # Inicializar contadores de servicio
        self.total_service_requests = 0
        self.successful_services = 0
        self.failed_services = 0
        self.timeout_services = 0

        for topic in ["solicitud_servicio", "registro_taxi", "posicion_taxi"]:
            self.subscriber.setsockopt_string(zmq.SUBSCRIBE, topic)

        self.taxis = {}  # {id_taxi: {'posicion': {}, 'estado': str, 'servicios_realizados': int, 'ultimo_update': float}}
        self.servicios_activos = {}
        self.registered_taxis = set()

        self.secondary_address = secondary_address
        self.update_taxi_metrics()

        # Verificar el estado del servidor primario
        if self.check_primary_health():
            self.is_primary = False
            self.logger.info("🚀 Servidor secundario iniciado en el puerto 50051")
        else:
            self.is_primary = True
            self.resume_server()
            self.logger.info("🚀 Servidor primario iniciado en el puerto 50051")

        self.active = True
        self.start_message_processing()

    def calcular_distancia(self, pos1, pos2):
        """Calcula la distancia euclidiana entre dos posiciones"""
        return math.sqrt(
            (pos1['lat'] - pos2['lat']) ** 2 +
            (pos1['lng'] - pos2['lng']) ** 2
        )

    def check_primary_health(self):
        try:
            with grpc.insecure_channel(self.secondary_address) as channel:
                stub = taxi_service_pb2_grpc.TaxiDatabaseServiceStub(channel)
                request = taxi_service_pb2.HealthCheckRequest()
                response = stub.HealthCheck(request)
                return response.status
        except Exception as e:
            return False

    def encontrar_taxi_mas_cercano(self, posicion_cliente):
        """Busca el taxi disponible más cercano usando gRPC"""
        try:
            # Obtener taxis disponibles del servicio de base de datos
            request = taxi_service_pb2.GetAvailableTaxisRequest()
            available_taxis_stream = self.db_stub.GetAvailableTaxis(request)

            # Procesar el stream de taxis disponibles
            taxis_disponibles = []
            current_time = time.time()

            for taxi in available_taxis_stream:
                # Verificar si el taxi está en nuestro sistema local y activo
                if (taxi.taxi_id in self.taxis and
                        self.taxis[taxi.taxi_id]['estado'] == 'AVAILABLE' and
                        current_time - self.taxis[taxi.taxi_id].get('ultimo_update', 0) < 60):
                    distancia = self.calcular_distancia(
                        {'lat': taxi.current_position.latitude, 'lng': taxi.current_position.longitude},
                        posicion_cliente
                    )
                    taxis_disponibles.append((taxi.taxi_id, distancia))

            if not taxis_disponibles:
                self.logger.warning("❌ No hay taxis activos disponibles")
                return None

            # Ordenar por distancia y seleccionar el más cercano
            taxi_elegido = min(taxis_disponibles, key=lambda x: x[1])[0]
            self.logger.info(f"✅ Taxi {taxi_elegido} asignado a cliente en posición {posicion_cliente}")
            return taxi_elegido

        except Exception as e:
            self.logger.error(f"❌ Error al buscar taxi disponible: {e}")
            return None

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
                            message_id = str(uuid.uuid4())  # o usar ID del mensaje si existe
                            self.metrics.record_message(topic)
                            self.metrics.record_message_timestamp('solicitud_servicio', message_id)
                            data = json.loads(message_data)
                            if topic == "solicitud_servicio":
                                self.metrics.record_message_timestamp('solicitud_servicio', message_id)
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
                self.metrics.service_counter.labels(status='completed').inc()
                self.metrics.service_outcomes.labels(outcome='success').inc()
                # Actualizar estado en la base de datos
                request = taxi_service_pb2.UpdateServiceRequest(
                    service_id=service_id,
                    status='COMPLETED',
                    completion_timestamp=str(datetime.fromtimestamp(data['timestamp'])),
                    taxi_id=data['id_taxi'],
                    final_position=taxi_service_pb2.Position(
                        latitude=data['posicion']['lat'],
                        longitude=data['posicion']['lng'],
                        timestamp=str(datetime.fromtimestamp(data['timestamp']))
                    )
                )
                response = self.db_stub.UpdateService(request)

                if response.success:
                    # Actualizar successful_services del taxi
                    update_taxi_request = taxi_service_pb2.UpdateTaxiStatsRequest(
                        taxi_id=data['id_taxi'],
                        increment_successful=True
                    )
                    self.db_stub.UpdateTaxiStats(update_taxi_request)

                    # Resto del código existente...
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

                    if data['id_taxi'] in self.taxis:
                        self.taxis[data['id_taxi']]['estado'] = 'AVAILABLE'
                        self.taxis[data['id_taxi']]['posicion'] = data.get('posicion_final',
                                                                           self.taxis[data['id_taxi']]['posicion'])

                    del self.servicios_activos[service_id]
                    self.logger.info(f"✅ Servicio {service_id} completado correctamente")

                    start_time = self.servicios_activos[service_id].get('start_time', time.time())
                    service_duration = time.time() - start_time
                    self.metrics.service_processing_time.labels(service_type='complete').observe(
                        service_duration * 1000)
                    # Replicar estado después de la actualización
                    self.replicate_state()
                else:
                    self.metrics.service_outcomes.labels(outcome='failure').inc()
                    self.logger.error(f"❌ Error al actualizar servicio en la base de datos: {response.message}")

            else:
                self.metrics.service_outcomes.labels(outcome='failure').inc()
                self.logger.warning(f"⚠️ Servicio {service_id} no encontrado en servicios activos")

        except Exception as e:
            self.metrics.service_outcomes.labels(outcome='error').inc()
            self.logger.error(f"❌ Error al manejar completación de servicio: {e}")

    def update_taxi_metrics(self):
        """Actualiza todas las métricas relacionadas con taxis activos"""
        try:
            # Contar taxis por estado
            available_count = sum(1 for taxi in self.taxis.values() if taxi['estado'] == 'AVAILABLE')
            busy_count = sum(1 for taxi in self.taxis.values() if taxi['estado'] == 'BUSY')
            total_count = len(self.taxis)

            # Actualizar métricas de taxis activos
            self.metrics.active_taxis.labels(status='available').set(available_count)
            self.metrics.active_taxis.labels(status='busy').set(busy_count)
            self.metrics.active_taxis.labels(status='total').set(total_count)

            # Calcular y actualizar tasa de ocupación
            if total_count > 0:
                occupation_rate = (busy_count / total_count) * 100
                self.metrics.active_taxis.labels(status='occupation_rate').set(occupation_rate)

        except Exception as e:
            self.logger.error(f"❌ Error al actualizar métricas de taxis: {e}")

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
        try:
            start_time = time.time()
            self.total_service_requests += 1
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
                service_id=service_id,
                client_id=data['id_cliente'],
                client_position=taxi_service_pb2.Position(
                    latitude=data['posicion']['lat'],
                    longitude=data['posicion']['lng'],
                    timestamp=str(datetime.fromtimestamp(data['timestamp']))
                ),
                status='REQUESTED',
                taxi_id=taxi_id,
                taxi_initial_position=taxi_service_pb2.Position(
                    latitude=self.taxis[taxi_id]['posicion']['lat'],
                    longitude=self.taxis[taxi_id]['posicion']['lng'],
                    timestamp=str(datetime.fromtimestamp(time.time()))
                )
            )
            response = self.db_stub.CreateService(request)

            if response.success:
                self.successful_services += 1
                self.metrics.record_service('requested')
                self.metrics.service_counter.labels(status='completed').inc()
                self.metrics.service_outcomes.labels(outcome='success').inc()
                success_rate = (self.successful_services / self.total_service_requests) * 100
                self.metrics.service_counter.labels(status='success_rate').inc(success_rate)

                # Actualizar contador total_services del taxi
                update_taxi_request = taxi_service_pb2.UpdateTaxiStatsRequest(
                    taxi_id=taxi_id,
                    increment_total=True
                )
                self.db_stub.UpdateTaxiStats(update_taxi_request)

                self.taxis[taxi_id]['estado'] = 'BUSY'
                self.servicios_activos[service_id] = {
                    'taxi_id': taxi_id,
                    'client_id': data['id_cliente'],
                    'start_time': time.time(),
                    'status': 'ASSIGNED'
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
                self.logger.info(f"✅ Servicio {service_id} asignado al taxi {taxi_id}")

                duration = time.time() - start_time
                self.metrics.assignment_time.observe(duration * 1000)
                distance = self.calcular_distancia(
                    self.taxis[taxi_id]['posicion'],
                    data['posicion']
                )
                self.metrics.distance_to_client.observe(distance)
                self.update_taxi_metrics()
                # Replicar estado después de la actualización
                self.replicate_state()

            else:
                self.failed_services += 1
                self.metrics.service_counter.labels(status='failed').inc()
                self.metrics.service_outcomes.labels(outcome='failure').inc()
                error_message = {
                    'tipo': 'resultado_servicio',
                    'subtipo': 'error',
                    'id_cliente': data['id_cliente'],
                    'mensaje': 'Error al crear el servicio'
                }
                self.publisher.send_string(f"resultado_servicio {json.dumps(error_message)}")

        except Exception as e:
            self.failed_services += 1
            self.metrics.service_counter.labels(status='failed').inc()
            self.metrics.service_outcomes.labels(outcome='error').inc()
            self.logger.error(f"❌ Error al manejar solicitud de servicio: {e}")
            error_message = {
                'tipo': 'resultado_servicio',
                'subtipo': 'error',
                'id_cliente': data['id_cliente'],
                'mensaje': f'Error interno: {str(e)}'
            }
            self.publisher.send_string(f"resultado_servicio {json.dumps(error_message)}")

        finally:
            duration_ms = (time.time() - start_time) * 1000
            self.metrics.record_service_outcome('success', duration_ms)

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
                    # Replicar estado después de la actualización
                    self.replicate_state()

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
                        # Replicar estado después de la actualización
                        self.replicate_state()

                else:
                    self.logger.error(f"❌ Error al actualizar posición: {response.message}")

        except Exception as e:
            self.logger.error(f"❌ Error en actualización de posición: {e}")

    def cleanup_completed_services(self):
        """Limpia los servicios completados y restaura el estado de los taxis"""
        current_time = time.time()
        timeout_services = 0
        completed_services = []

        for service_id, service_info in list(self.servicios_activos.items()):
            # Timeout de 30 minutos en lugar de 30 segundos
            if current_time - service_info['start_time'] >= 1800:  # 30 * 60 seconds
                taxi_id = service_info['taxi_id']
                self.timeout_services += 1
                timeout_services += 1
                self.metrics.service_counter.labels(status='timeout').inc()
                self.metrics.service_outcomes.labels(outcome='timeout').inc()
                if taxi_id in self.taxis:
                    # Intentar completar automáticamente el servicio
                    try:
                        self.handle_service_completion({
                            'id_servicio': service_id,
                            'id_taxi': taxi_id,
                            'timestamp': current_time,
                            'posicion_final': self.taxis[taxi_id]['posicion']
                        })
                        completed_services.append(service_id)
                    except Exception as e:
                        self.logger.error(f"Error completando servicio automáticamente: {e}")

        if timeout_services > 0:
            self.update_taxi_metrics()
            timeout_rate = (self.timeout_services / self.total_service_requests) * 100
            self.metrics.service_counter.labels(status='timeout_rate').set(timeout_rate)

        for service_id in completed_services:
            del self.servicios_activos[service_id]

    def replicate_state(self):
        """Replica el estado al servidor secundario"""
        if not self.is_primary:
            return

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
                self.logger.error(f"❌ Error replicando estado a {self.secondary_address}")
                time.sleep(1)
                self.replicate_state()

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
            self.logger.error(f"❌ Error al recibir replicación")
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
        # Re-suscribir a los tópicos de mensajes
        for topic in ["solicitud_servicio", "registro_taxi", "posicion_taxi", "servicio_completado"]:
            self.subscriber.setsockopt_string(zmq.SUBSCRIBE, topic)

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



    def run(self, port=50051):
        server = grpc.server(futures.ThreadPoolExecutor(max_workers=10))
        taxi_service_pb2_grpc.add_TaxiDatabaseServiceServicer_to_server(self, server)
        server.add_insecure_port(f'[::]:{port}')
        server.start()
        #self.logger.info(f"🚀 Servidor secundario iniciado en puerto {port}")

        try:
            while True:
                time.sleep(1)
                if self.is_primary:
                    self.cleanup_completed_services()
                    #self.replicate_state()
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
    server = TaxiServer()
    try:
        server.run()
    except KeyboardInterrupt:
        print("\n🛑 Servidor detenido por el usuario")
    except Exception as e:
        print(f"❌ Error: {e}")


if __name__ == "__main__":
    main()
