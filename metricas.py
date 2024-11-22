from prometheus_client import Counter, Histogram, Gauge, start_http_server
import time
from functools import wraps
from datetime import datetime


class TaxiMetrics:
    def __init__(self, port=8000):
        # Iniciar servidor de métricas Prometheus
        start_http_server(port)

        # Métricas de solicitudes de servicio
        self.service_request_total = Counter(
            'taxi_service_requests_total',
            'Total number of taxi service requests',
            ['status']  # 'success', 'failed', 'timeout'
        )

        # Tiempo de respuesta para solicitudes
        self.service_response_time = Histogram(
            'taxi_service_response_time_seconds',
            'Time spent processing service requests',
            ['request_type'],
            buckets=(0.1, 0.5, 1.0, 2.0, 5.0, 10.0, float("inf"))
        )

        # Métricas de taxis
        self.active_taxis = Gauge(
            'taxi_active_taxis',
            'Number of currently active taxis',
            ['status']  # 'available', 'busy'
        )

        # Métricas de mensajes
        self.message_total = Counter(
            'taxi_messages_total',
            'Total number of messages processed',
            ['topic', 'status']  # topic: solicitud_servicio, registro_taxi, etc.
        )

        # Métricas de servicios completados
        self.completed_services = Counter(
            'taxi_completed_services_total',
            'Total number of completed services',
            ['success']  # 'true', 'false'
        )

        # Tiempo promedio de asignación
        self.assignment_time = Histogram(
            'taxi_assignment_time_seconds',
            'Time to assign a taxi to a request',
            buckets=(1.0, 5.0, 10.0, 30.0, 60.0, float("inf"))
        )

        # Distancia promedio al cliente
        self.distance_to_client = Histogram(
            'taxi_distance_to_client_meters',
            'Distance between taxi and client at assignment',
            buckets=(100, 500, 1000, 2000, 5000, float("inf"))
        )

    def measure_time(self, request_type):
        def decorator(func):
            @wraps(func)
            def wrapper(*args, **kwargs):
                start_time = time.time()
                result = func(*args, **kwargs)
                duration = time.time() - start_time
                self.service_response_time.labels(request_type=request_type).observe(duration)
                return result

            return wrapper

        return decorator

    def record_message(self, topic, status='success'):
        """Registra un mensaje procesado"""
        self.message_total.labels(topic=topic, status=status).inc()

    def record_service_request(self, status):
        """Registra una solicitud de servicio"""
        self.service_request_total.labels(status=status).inc()

    def update_active_taxis(self, available_count, busy_count):
        """Actualiza el contador de taxis activos"""
        self.active_taxis.labels(status='available').set(available_count)
        self.active_taxis.labels(status='busy').set(busy_count)

    def record_service_completion(self, success=True):
        """Registra un servicio completado"""
        self.completed_services.labels(success=str(success).lower()).inc()

    def record_assignment_time(self, duration):
        """Registra el tiempo de asignación de un taxi"""
        self.assignment_time.observe(duration)

    def record_distance_to_client(self, distance):
        """Registra la distancia entre el taxi y el cliente"""
        self.distance_to_client.observe(distance)