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
                        message="Database connection healthy",
                        timestamp=datetime.now().isoformat()
                    )
        except Exception as e:
            return taxi_service_pb2.HealthCheckResponse(
                status=False,
                message=str(e),
                timestamp=datetime.now().isoformat()
            )

    def register_taxi(self, request, context):
        try:
            logging.info(f"Registrando nuevo taxi con ID: {request.taxi_id}")
            with self._pool.getconn() as conn:
                with conn.cursor() as cur:
                    taxi_id = str(uuid.uuid4()) if not request.taxi_id else request.taxi_id
                    cur.execute("""
                        INSERT INTO taxis (taxi_id, status, last_update)
                        VALUES (%s, %s, CURRENT_TIMESTAMP)
                        RETURNING taxi_id
                    """, (taxi_id, request.status or 'AVAILABLE'))

                    cur.execute("""
                        INSERT INTO taxi_locations (taxi_id, latitude, longitude)
                        VALUES (%s, %s, %s)
                    """, (
                        taxi_id,
                        request.initial_position.latitude,
                        request.initial_position.longitude
                    ))
                    conn.commit()
                    logging.info(f"Taxi {taxi_id} registrado exitosamente")

            return taxi_service_pb2.RegisterTaxiResponse(
                success=True,
                message=f"Taxi registered successfully",
                taxi_id=taxi_id
            )
        except Exception as e:
            logging.error(f"Error al registrar taxi: {e}")
            return taxi_service_pb2.RegisterTaxiResponse(
                success=False,
                message=str(e),
                taxi_id=""
            )

    def UpdateTaxiPosition(self, request, context):
        try:
            logging.info(f"Actualizando posición del taxi {request.taxi_id}")
            with self._pool.getconn() as conn:
                with conn.cursor() as cur:
                    # Verificar si el taxi existe
                    cur.execute("SELECT taxi_id FROM taxis WHERE taxi_id = %s", (request.taxi_id,))
                    if not cur.fetchone():
                        context.abort(grpc.StatusCode.NOT_FOUND, "Taxi not found")

                    # Actualizar estado del taxi
                    cur.execute("""
                        UPDATE taxis 
                        SET last_update = CURRENT_TIMESTAMP,
                            status = %s
                        WHERE taxi_id = %s
                    """, (request.status, request.taxi_id))

                    # Registrar nueva posición
                    cur.execute("""
                        INSERT INTO taxi_locations (taxi_id, latitude, longitude, timestamp)
                        VALUES (%s, %s, %s, %s)
                        RETURNING latitude, longitude, timestamp
                    """, (
                        request.taxi_id,
                        request.position.latitude,
                        request.position.longitude,
                        datetime.now() if not request.timestamp else datetime.fromisoformat(request.timestamp)
                    ))

                    lat, lon, timestamp = cur.fetchone()
                    conn.commit()
                    logging.info(f"Posición del taxi {request.taxi_id} actualizada exitosamente")

                    # Crear objeto Position para la respuesta
                    confirmed_position = taxi_service_pb2.Position(
                        latitude=lat,
                        longitude=lon,
                        timestamp=timestamp.isoformat()
                    )

                    return taxi_service_pb2.UpdateTaxiPositionResponse(
                        success=True,
                        message="Position updated successfully",
                        confirmed_position=confirmed_position
                    )
        except Exception as e:
            logging.error(f"Error al actualizar posición: {e}")
            return taxi_service_pb2.UpdateTaxiPositionResponse(
                success=False,
                message=str(e),
                confirmed_position=None
            )

    def CreateService(self, request, context):
        try:
            with self._pool.getconn() as conn:
                with conn.cursor() as cur:
                    service_id = str(uuid.uuid4())
                    # Buscar taxi disponible más cercano
                    cur.execute("""
                        SELECT t.taxi_id, tl.latitude, tl.longitude
                        FROM taxis t
                        JOIN taxi_locations tl ON t.taxi_id = tl.taxi_id
                        WHERE t.status = 'AVAILABLE'
                        ORDER BY 
                            point(%s, %s) <-> point(tl.latitude, tl.longitude)
                        LIMIT 1
                    """, (request.client_position.latitude, request.client_position.longitude))

                    result = cur.fetchone()
                    if not result:
                        return taxi_service_pb2.CreateServiceResponse(
                            success=False,
                            message="No available taxis found"
                        )

                    taxi_id, taxi_lat, taxi_lon = result

                    # Crear el servicio
                    cur.execute("""
                        INSERT INTO services (
                            service_id, taxi_id, client_id, status,
                            start_position_lat, start_position_lng
                        ) VALUES (%s, %s, %s, 'ASSIGNED', %s, %s)
                    """, (
                        service_id, taxi_id, request.client_id,
                        request.client_position.latitude,
                        request.client_position.longitude
                    ))

                    # Actualizar estado del taxi
                    cur.execute("""
                        UPDATE taxis
                        SET status = 'BUSY'
                        WHERE taxi_id = %s
                    """, (taxi_id,))

                    conn.commit()

                    taxi_position = taxi_service_pb2.Position(
                        latitude=taxi_lat,
                        longitude=taxi_lon
                    )

                    return taxi_service_pb2.CreateServiceResponse(
                        success=True,
                        service_id=service_id,
                        assigned_taxi_id=taxi_id,
                        message="Service created successfully",
                        taxi_position=taxi_position
                    )
        except Exception as e:
            logging.error(f"Error creating service: {e}")
            return taxi_service_pb2.CreateServiceResponse(
                success=False,
                message=str(e)
            )

    def GetAvailableTaxis(self, request, context):
        try:
            with self._pool.getconn() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        SELECT t.taxi_id, t.status, t.total_services, t.successful_services,
                               tl.latitude, tl.longitude, tl.timestamp
                        FROM taxis t
                        JOIN taxi_locations tl ON t.taxi_id = tl.taxi_id
                        WHERE t.status = 'AVAILABLE'
                        AND point(%s, %s) <-> point(tl.latitude, tl.longitude) <= %s
                    """, (
                        request.reference_position.latitude,
                        request.reference_position.longitude,
                        request.radius
                    ))

                    for row in cur:
                        taxi_id, status, total_services, successful_services, lat, lon, timestamp = row
                        position = taxi_service_pb2.Position(
                            latitude=lat,
                            longitude=lon,
                            timestamp=timestamp.isoformat()
                        )

                        yield taxi_service_pb2.Taxi(
                            taxi_id=taxi_id,
                            status=status,
                            current_position=position,
                            initial_position=position,  # Podríamos obtener la posición inicial real si es necesario
                            total_services=total_services,
                            successful_services=successful_services
                        )
        except Exception as e:
            logging.error(f"Error getting available taxis: {e}")
            context.abort(grpc.StatusCode.INTERNAL, str(e))


def serve():
    """Inicia el servidor gRPC del gestor de base de datos"""
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=10))
    gestor_service = GestorDBService()
    taxi_service_pb2_grpc.add_TaxiDatabaseServiceServicer_to_server(gestor_service, server)
    server.add_insecure_port("[::]:50052")
    server.start()
    logging.info("DB manager started on port 50052")

    try:
        server.wait_for_termination()
    except KeyboardInterrupt:
        server.stop(0)
        logging.info("DB manager stopped")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    serve()