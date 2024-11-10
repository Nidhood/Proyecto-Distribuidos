import logging
import grpc
from concurrent import futures
import uuid
import psycopg2
from psycopg2 import pool
from datetime import datetime
import taxi_service_pb2
import taxi_service_pb2_grpc

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class DatabaseManager(taxi_service_pb2_grpc.TaxiDatabaseServiceServicer):
    def __init__(self):
        self.connection_pool = self.create_connection_pool()

    def create_connection_pool(self):
        try:
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
        except Exception as e:
            logger.error(f"Error creating connection pool: {str(e)}")
            raise

    def get_connection(self):
        return self.connection_pool.getconn()

    def return_connection(self, conn):
        self.connection_pool.putconn(conn)

    def HealthCheck(self, request, context):
        conn = None
        try:
            conn = self.get_connection()
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
                return taxi_service_pb2.HealthCheckResponse(
                    status=True,
                    message="Database connection successful",
                    timestamp=datetime.now().isoformat()
                )
        except Exception as e:
            logger.error(f"Health check failed: {str(e)}")
            return taxi_service_pb2.HealthCheckResponse(
                status=False,
                message=f"Database connection failed: {str(e)}",
                timestamp=datetime.now().isoformat()
            )
        finally:
            if conn:
                self.return_connection(conn)

    def RegisterTaxi(self, request, context):
        conn = None
        try:
            conn = self.get_connection()
            with conn.cursor() as cur:
                # Verificar si el taxi ya existe
                cur.execute("SELECT taxi_id FROM taxis WHERE taxi_id = %s", (request.taxi_id,))
                if cur.fetchone():
                    return taxi_service_pb2.RegisterTaxiResponse(
                        success=False,
                        message="Taxi already registered",
                        taxi_id=request.taxi_id
                    )

                # Registrar el nuevo taxi
                cur.execute("""
                    INSERT INTO taxis (
                        taxi_id, 
                        status, 
                        registration_date,
                        last_update,
                        total_services,
                        successful_services
                    ) VALUES (%s, %s, %s, %s, 0, 0)
                    """, (
                    request.taxi_id,
                    request.status or 'AVAILABLE',
                    datetime.now(),
                    datetime.now()
                ))

                # Registrar la posición inicial
                cur.execute("""
                    INSERT INTO taxi_locations (
                        taxi_id,
                        latitude,
                        longitude,
                        timestamp
                    ) VALUES (%s, %s, %s, %s)
                    """, (
                    request.taxi_id,
                    request.initial_position.latitude,
                    request.initial_position.longitude,
                    datetime.now()
                ))

                conn.commit()
                logger.info(f"Taxi {request.taxi_id} registrado exitosamente")
                return taxi_service_pb2.RegisterTaxiResponse(
                    success=True,
                    message="Taxi registered successfully",
                    taxi_id=request.taxi_id
                )

        except Exception as e:
            if conn:
                conn.rollback()
            logger.error(f"Error al registrar taxi: {str(e)}")
            return taxi_service_pb2.RegisterTaxiResponse(
                success=False,
                message=f"Error registering taxi: {str(e)}",
                taxi_id=request.taxi_id
            )
        finally:
            if conn:
                self.return_connection(conn)

    def UpdateTaxiPosition(self, request, context):
        conn = None
        try:
            conn = self.get_connection()
            with conn.cursor() as cur:
                # Verificar si el taxi existe
                cur.execute("SELECT taxi_id FROM taxis WHERE taxi_id = %s", (request.taxi_id,))
                if not cur.fetchone():
                    context.set_code(grpc.StatusCode.NOT_FOUND)
                    context.set_details("Taxi not found")
                    return taxi_service_pb2.UpdateTaxiPositionResponse(
                        success=False,
                        message="Taxi not found"
                    )

                # Registrar nueva posición
                cur.execute("""
                    INSERT INTO taxi_locations (
                        taxi_id,
                        latitude,
                        longitude,
                        timestamp
                    ) VALUES (%s, %s, %s, %s)
                    RETURNING latitude, longitude
                    """, (
                    request.taxi_id,
                    request.position.latitude,
                    request.position.longitude,
                    datetime.fromisoformat(request.timestamp) if request.timestamp else datetime.now()
                ))

                lat, lng = cur.fetchone()

                # Actualizar estado del taxi si se proporciona
                if request.status:
                    cur.execute("""
                        UPDATE taxis 
                        SET status = %s, last_update = %s 
                        WHERE taxi_id = %s
                        """, (request.status, datetime.now(), request.taxi_id))

                conn.commit()
                logger.info(f"Posición del taxi {request.taxi_id} actualizada")

                return taxi_service_pb2.UpdateTaxiPositionResponse(
                    success=True,
                    message="Position updated successfully",
                    confirmed_position=taxi_service_pb2.Position(
                        latitude=lat,
                        longitude=lng,
                        timestamp=datetime.now().isoformat()
                    )
                )

        except Exception as e:
            if conn:
                conn.rollback()
            logger.error(f"Error al actualizar posición: {str(e)}")
            return taxi_service_pb2.UpdateTaxiPositionResponse(
                success=False,
                message=f"Error updating position: {str(e)}"
            )
        finally:
            if conn:
                self.return_connection(conn)

    def CreateService(self, request, context):
        conn = None
        try:
            conn = self.get_connection()
            with conn.cursor() as cur:
                service_id = str(uuid.uuid4())
                cur.execute("""
                    INSERT INTO services (
                        service_id,
                        status,
                        request_timestamp,
                        client_latitude,
                        client_longitude
                    ) VALUES (%s, 'REQUESTED', %s, %s, %s)
                    RETURNING service_id
                    """, (
                    service_id,
                    datetime.fromisoformat(request.timestamp) if request.timestamp else datetime.now(),
                    request.client_position.latitude,
                    request.client_position.longitude
                ))

                conn.commit()
                return taxi_service_pb2.CreateServiceResponse(
                    success=True,
                    service_id=service_id,
                    message="Service created successfully"
                )

        except Exception as e:
            if conn:
                conn.rollback()
            logger.error(f"Error al crear servicio: {str(e)}")
            return taxi_service_pb2.CreateServiceResponse(
                success=False,
                message=f"Error creating service: {str(e)}"
            )
        finally:
            if conn:
                self.return_connection(conn)

    def GetAvailableTaxis(self, request, context):
        conn = None
        try:
            conn = self.get_connection()
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT t.taxi_id, t.status, t.total_services, t.successful_services,
                           l.latitude, l.longitude, l.timestamp
                    FROM taxis t
                    JOIN taxi_locations l ON t.taxi_id = l.taxi_id
                    WHERE t.status = 'AVAILABLE'
                    AND l.timestamp = (
                        SELECT MAX(timestamp)
                        FROM taxi_locations l2
                        WHERE l2.taxi_id = t.taxi_id
                    )
                    """)

                for row in cur.fetchall():
                    taxi_id, status, total_services, successful_services, lat, lng, timestamp = row
                    yield taxi_service_pb2.Taxi(
                        taxi_id=taxi_id,
                        status=status,
                        current_position=taxi_service_pb2.Position(
                            latitude=float(lat),
                            longitude=float(lng),
                            timestamp=timestamp.isoformat()
                        ),
                        total_services=total_services,
                        successful_services=successful_services
                    )

        except Exception as e:
            logger.error(f"Error al obtener taxis disponibles: {str(e)}")
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details(f"Error getting available taxis: {str(e)}")
        finally:
            if conn:
                self.return_connection(conn)


def serve():
    try:
        server = grpc.server(futures.ThreadPoolExecutor(max_workers=10))
        taxi_service_pb2_grpc.add_TaxiDatabaseServiceServicer_to_server(
            DatabaseManager(), server)
        server.add_insecure_port('[::]:50052')
        server.start()
        logger.info("DB manager started on port 50052")
        server.wait_for_termination()

    except KeyboardInterrupt:
        server.stop(0)
        logger.info("DB manager stopped by user")

if __name__ == '__main__':
    serve()