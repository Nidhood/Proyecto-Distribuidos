import grpc
from concurrent import futures
import logging
import psycopg2
from psycopg2 import pool
import uuid
from datetime import datetime

import taxi_service_pb2
import taxi_service_pb2_grpc


def _initialize_pool():
    return psycopg2.pool.SimpleConnectionPool(
        minconn=1,
        maxconn=10,
        host='droll-rabbit-5432.7tt.aws-us-east-1.cockroachlabs.cloud',
        port=26257,
        database='my_uber',
        user='nidhood',
        password='KUukuDVmnSeSOB411JLJwg',
        sslmode='verify-full',
        sslrootcert='certificate/root.crt'
    )


class GestorDBService(taxi_service_pb2_grpc.TaxiDatabaseServiceServicer):
    def __init__(self):
        self._pool = _initialize_pool()

    def HealthCheck(self, request, context):
        try:
            with self._pool.getconn() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT 1")
                    return taxi_service_pb2.HealthCheckResponse(
                        status=True,
                        message="Database connection healthy"
                    )
        except Exception as e:
            return taxi_service_pb2.HealthCheckResponse(
                status=False,
                message=str(e)
            )

    def register_taxi(self, request, context):
        try:
            logging.info(f"Registrando nuevo taxi con ID: {request.taxi_id}")
            with self._pool.getconn() as conn:
                with conn.cursor() as cur:
                    taxi_id = str(uuid.uuid4()) if not request.taxi_id else request.taxi_id
                    cur.execute("""
                        INSERT INTO taxis (taxi_id, status, last_update)
                        VALUES (%s, 'AVAILABLE', CURRENT_TIMESTAMP)
                        RETURNING taxi_id
                    """, (taxi_id,))

                    cur.execute("""
                        INSERT INTO taxi_locations (taxi_id, latitude, longitude)
                        VALUES (%s, %s, %s)
                    """, (
                        taxi_id,
                        request.initial_position.latitude,
                        request.initial_position.longitude
                    ))
                    conn.commit()
                    logging.info(f"Taxi {taxi_id} registrado exitosamente en la base de datos")

            return taxi_service_pb2.RegisterTaxiResponse(
                success=True,
                message=f"Taxi registered with ID: {taxi_id}"
            )
        except Exception as e:
            logging.error(f"Error al registrar taxi en la base de datos: {e}")
            return taxi_service_pb2.RegisterTaxiResponse(
                success=False,
                message=str(e)
            )

    def update_taxi_position(self, request, context):
        try:
            logging.info(f"Actualizando posición del taxi {request.taxi_id}")
            with self._pool.getconn() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        UPDATE taxis 
                        SET last_update = CURRENT_TIMESTAMP,
                            status = %s
                        WHERE taxi_id = %s
                    """, (request.status, request.taxi_id))

                    cur.execute("""
                        INSERT INTO taxi_locations (taxi_id, latitude, longitude)
                        VALUES (%s, %s, %s)
                    """, (
                        request.taxi_id,
                        request.position.latitude,
                        request.position.longitude
                    ))
                    conn.commit()
                    logging.info(f"Posición del taxi {request.taxi_id} actualizada exitosamente")

            return taxi_service_pb2.UpdateTaxiPositionResponse(
                success=True,
                message="Position updated successfully"
            )
        except Exception as e:
            logging.error(f"Error al actualizar posición del taxi: {e}")
            return taxi_service_pb2.UpdateTaxiPositionResponse(
                success=False,
                message=str(e)
            )

    def get_statistics(self, request, context):
        try:
            with self._pool.getconn() as conn:
                with conn.cursor() as cur:
                    # Estadísticas globales
                    cur.execute("""
                        SELECT 
                            COUNT(*) as total,
                            SUM(CASE WHEN status = 'COMPLETED' THEN 1 ELSE 0 END) as completed,
                            SUM(CASE WHEN status = 'DENIED' THEN 1 ELSE 0 END) as denied
                        FROM services
                    """)
                    total, completed, denied = cur.fetchone()

                    # Estadísticas por taxi
                    taxi_stats = {}
                    cur.execute("""
                        SELECT 
                            t.taxi_id,
                            t.total_services,
                            t.successful_services,
                            json_agg(
                                json_build_object(
                                    'latitude', tl.latitude,
                                    'longitude', tl.longitude,
                                    'timestamp', tl.timestamp
                                )
                            ) as positions
                        FROM taxis t
                        LEFT JOIN taxi_locations tl ON t.taxi_id = tl.taxi_id
                        GROUP BY t.taxi_id, t.total_services, t.successful_services
                    """)

                    for row in cur.fetchall():
                        taxi_id, total_services, successful_services, positions = row
                        taxi_stats[taxi_id] = taxi_service_pb2.TaxiStats(
                            total_services=total_services,
                            successful_services=successful_services,
                            historical_positions=[
                                taxi_service_pb2.Position(
                                    latitude=pos['latitude'],
                                    longitude=pos['longitude'],
                                    timestamp=str(pos['timestamp'])
                                ) for pos in positions
                            ]
                        )

            return taxi_service_pb2.GetStatisticsResponse(
                total_services=total,
                completed_services=completed,
                denied_services=denied,
                taxi_statistics=taxi_stats
            )
        except Exception as e:
            context.abort(grpc.StatusCode.INTERNAL, str(e))


def serve():
    """Inicia el servidor gRPC del gestor de base de datos"""
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=10))
    gestor_service = GestorDBService()
    taxi_service_pb2_grpc.add_TaxiDatabaseServiceServicer_to_server(gestor_service, server)

    # Cambiar el puerto a 50052 para el gestor_db
    server.add_insecure_port("[::]:50052")
    server.start()
    logging.info("DB manager started on port 50052")

    # Mantener el servidor corriendo
    try:
        server.wait_for_termination()
    except KeyboardInterrupt:
        server.stop(0)
        logging.info("DB manager stopped")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    serve()
