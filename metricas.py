from prometheus_client import Counter, Histogram, Gauge, start_http_server
import time
from functools import wraps
from datetime import datetime


class TaxiMetrics:
    def __init__(self, port=8000):
        # Iniciar servidor de métricas Prometheus
        start_http_server(port)

        # Métricas de latencia de mensajes (en milisegundos)
        self.message_latency = Histogram(
            'taxi_message_latency_milliseconds',
            'Time between message send and receive in milliseconds',
            ['message_type'],
            buckets=(1, 5, 10, 25, 50, 100, 250, 500, 1000, float("inf"))
        )

        # Contador total de mensajes por tipo
        self.message_counter = Counter(
            'taxi_messages_total',
            'Total number of messages processed',
            ['message_type', 'status']  # message_type: solicitud_servicio, registro_taxi, etc.
        )

        # Tasa de mensajes por segundo
        self.message_rate = Gauge(
            'taxi_message_rate_per_second',
            'Current rate of messages per second',
            ['message_type']
        )

        # Métricas de servicios
        self.service_counter = Counter(
            'taxi_services_total',
            'Total number of taxi services',
            ['status']  # requested, completed, failed
        )

        # Métricas de éxito/fallo de servicios
        self.service_outcomes = Counter(
            'taxi_service_outcomes_total',
            'Outcomes of taxi services',
            ['outcome']  # success, failure, timeout
        )

        # Tiempo de procesamiento de servicios (en milisegundos)
        self.service_processing_time = Histogram(
            'taxi_service_processing_milliseconds',
            'Time to process service requests in milliseconds',
            ['service_type'],
            buckets=(10, 50, 100, 250, 500, 1000, 2500, 5000, float("inf"))
        )

        # Tráfico de mensajes por minuto
        self.traffic_gauge = Gauge(
            'taxi_traffic_messages_per_minute',
            'Number of messages processed per minute',
            ['message_type']
        )

        # Métricas de taxis activos
        self.active_taxis = Gauge(
            'taxi_active_count',
            'Number of currently active taxis',
            ['status']  # available, busy, total
        )

        # Tiempo de asignación de taxis (en milisegundos)
        self.assignment_time = Histogram(
            'taxi_assignment_time_milliseconds',
            'Time to assign a taxi to a request in milliseconds',
            buckets=(100, 250, 500, 1000, 2500, 5000, 10000, float("inf"))
        )

        # Distancia al cliente (en metros)
        self.distance_to_client = Histogram(
            'taxi_distance_to_client_meters',
            'Distance between taxi and client at assignment in meters',
            buckets=(100, 250, 500, 1000, 2000, 5000, float("inf"))
        )

        # Variables para cálculo de tráfico
        self.message_timestamps = {}
        self.last_traffic_update = time.time()
        self.message_count_window = {}

    def record_message_timestamp(self, message_type, message_id, timestamp=None):
        """Registra timestamp de envío/recepción de mensaje"""
        if timestamp is None:
            timestamp = time.time()

        if message_id not in self.message_timestamps:
            self.message_timestamps[message_id] = {'send': timestamp}
        else:
            # Calcular latencia en milisegundos
            latency = (timestamp - self.message_timestamps[message_id]['send']) * 1000
            self.message_latency.labels(message_type=message_type).observe(latency)
            del self.message_timestamps[message_id]

    def record_message(self, message_type, status='success'):
        """Registra un mensaje procesado y actualiza métricas de tráfico"""
        self.message_counter.labels(message_type=message_type, status=status).inc()

        # Actualizar conteo para cálculo de tráfico
        current_time = time.time()
        if message_type not in self.message_count_window:
            self.message_count_window[message_type] = []

        # Añadir timestamp actual
        self.message_count_window[message_type].append(current_time)

        # Limpiar timestamps más antiguos que 1 minuto
        self.message_count_window[message_type] = [
            ts for ts in self.message_count_window[message_type]
            if current_time - ts <= 60
        ]

        # Actualizar tráfico por minuto
        messages_per_minute = len(self.message_count_window[message_type])
        self.traffic_gauge.labels(message_type=message_type).set(messages_per_minute)

        # Actualizar tasa de mensajes por segundo
        if current_time - self.last_traffic_update >= 1.0:
            rate = messages_per_minute / 60.0  # convertir a mensajes por segundo
            self.message_rate.labels(message_type=message_type).set(rate)
            self.last_traffic_update = current_time

    def record_service(self, service_type='requested'):
        """Registra un nuevo servicio"""
        self.service_counter.labels(status=service_type).inc()

    def record_service_outcome(self, outcome, processing_time_ms):
        """Registra el resultado de un servicio y su tiempo de procesamiento"""
        self.service_outcomes.labels(outcome=outcome).inc()
        self.service_processing_time.labels(service_type='request').observe(processing_time_ms)

    def update_active_taxis(self, available_count, busy_count):
        """Actualiza el contador de taxis activos"""
        self.active_taxis.labels(status='available').set(available_count)
        self.active_taxis.labels(status='busy').set(busy_count)
        self.active_taxis.labels(status='total').set(available_count + busy_count)

    def record_assignment(self, duration_ms, distance_meters):
        """Registra tiempo de asignación y distancia al cliente"""
        self.assignment_time.observe(duration_ms)
        self.distance_to_client.observe(distance_meters)

    def measure_time_ms(self, metric_name):
        """Decorador para medir tiempo de ejecución en milisegundos"""

        def decorator(func):
            @wraps(func)
            def wrapper(*args, **kwargs):
                start_time = time.time()
                result = func(*args, **kwargs)
                duration_ms = (time.time() - start_time) * 1000
                self.service_processing_time.labels(service_type=metric_name).observe(duration_ms)
                return result

            return wrapper

        return decorator