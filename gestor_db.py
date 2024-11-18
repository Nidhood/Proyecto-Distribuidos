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
        logger.info("🚀 Initializing Taxi Database Manager...")
        self.connection_pool = self.create_connection_pool()
        logger.info("✅ Database Manager initialized successfully!")

    def create_connection_pool(self):
        try:
            logger.info("🔄 Creating database connection pool...")
            pool = psycopg2.pool.SimpleConnectionPool(
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
            logger.info("✅ Connection pool created successfully!")
            return pool
        except Exception as e:
            logger.error(f"❌ Failed to create connection pool: {str(e)}")
            raise

    def get_connection(self):
        conn = self.connection_pool.getconn()
        logger.debug("🔌 Retrieved database connection from pool")
        return conn

    def return_connection(self, conn):
        self.connection_pool.putconn(conn)
        logger.debug("↩️ Returned connection to pool")

    def HealthCheck(self, request, context):
        logger.info("🏥 Performing health check...")
        conn = None
        try:
            conn = self.get_connection()
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
                logger.info("💚 Health check passed!")
                return taxi_service_pb2.HealthCheckResponse(
                    status=True,
                    message="Database connection successful",
                    timestamp=datetime.now().isoformat()
                )
        except Exception as e:
            logger.error(f"🔴 Health check failed: {str(e)}")
            return taxi_service_pb2.HealthCheckResponse(
                status=False,
                message=f"Database connection failed: {str(e)}",
                timestamp=datetime.now().isoformat()
            )
        finally:
            if conn:
                self.return_connection(conn)

    def RegisterTaxi(self, request, context):
        logger.info(f"🚕 Attempting to register new taxi with ID: {request.taxi_id}")
        conn = None
        try:
            conn = self.get_connection()
            with conn.cursor() as cur:
                # Check if taxi exists
                cur.execute("SELECT taxi_id FROM taxis WHERE taxi_id = %s", (request.taxi_id,))
                if cur.fetchone():
                    logger.warning(f"⚠️ Taxi {request.taxi_id} already registered")
                    return taxi_service_pb2.RegisterTaxiResponse(
                        success=False,
                        message="Taxi already registered",
                        taxi_id=request.taxi_id
                    )

                # Register new taxi
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

                # Register initial position
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
                logger.info(f"✅ Taxi {request.taxi_id} registered successfully!")
                return taxi_service_pb2.RegisterTaxiResponse(
                    success=True,
                    message="Taxi registered successfully",
                    taxi_id=request.taxi_id
                )

        except Exception as e:
            if conn:
                conn.rollback()
            logger.error(f"❌ Error registering taxi: {str(e)}")
            return taxi_service_pb2.RegisterTaxiResponse(
                success=False,
                message=f"Error registering taxi: {str(e)}",
                taxi_id=request.taxi_id
            )
        finally:
            if conn:
                self.return_connection(conn)

    def UpdateTaxiPosition(self, request, context):
        logger.info(f"📍 Updating position for taxi {request.taxi_id}")
        conn = None
        try:
            conn = self.get_connection()
            with conn.cursor() as cur:
                # Check if taxi exists
                cur.execute("SELECT taxi_id FROM taxis WHERE taxi_id = %s", (request.taxi_id,))
                if not cur.fetchone():
                    logger.warning(f"⚠️ Taxi {request.taxi_id} not found")
                    context.set_code(grpc.StatusCode.NOT_FOUND)
                    context.set_details("Taxi not found")
                    return taxi_service_pb2.UpdateTaxiPositionResponse(
                        success=False,
                        message="Taxi not found"
                    )

                # Check if location record exists for this taxi
                cur.execute("""
                    SELECT taxi_id 
                    FROM taxi_locations 
                    WHERE taxi_id = %s
                    """, (request.taxi_id,))

                timestamp = datetime.fromisoformat(
                    request.position.timestamp) if request.position.timestamp else datetime.now()

                if cur.fetchone():
                    # Update existing location
                    cur.execute("""
                        UPDATE taxi_locations 
                        SET latitude = %s,
                            longitude = %s,
                            timestamp = %s
                        WHERE taxi_id = %s
                        RETURNING latitude, longitude
                        """, (
                        request.position.latitude,
                        request.position.longitude,
                        timestamp,
                        request.taxi_id
                    ))
                else:
                    # Insert new location if none exists
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
                        timestamp
                    ))

                lat, lng = cur.fetchone()

                # Update taxi status if provided
                if request.status:
                    cur.execute("""
                        UPDATE taxis 
                        SET status = %s, last_update = %s 
                        WHERE taxi_id = %s
                        """, (request.status, datetime.now(), request.taxi_id))

                conn.commit()
                logger.info(f"✅ Position updated for taxi {request.taxi_id} at ({lat}, {lng})")

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
            logger.error(f"❌ Error updating position: {str(e)}")
            return taxi_service_pb2.UpdateTaxiPositionResponse(
                success=False,
                message=f"Error updating position: {str(e)}"
            )
        finally:
            if conn:
                self.return_connection(conn)

    def CreateService(self, request, context):
        logger.info("🔔 Creating new service request")
        conn = None
        try:
            conn = self.get_connection()
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO services (
                        service_id,
                        taxi_id,
                        client_id,
                        status,
                        request_timestamp,
                        client_latitude,
                        client_longitude,
                        taxi_initial_latitude,
                        taxi_initial_longitude
                    ) VALUES (%s, %s, %s, 'REQUESTED', %s, %s, %s, %s, %s)
                    RETURNING service_id
                    """, (
                    request.service_id,
                    request.taxi_id,
                    request.client_id,
                    datetime.fromisoformat(request.timestamp) if request.timestamp else datetime.now(),
                    request.client_position.latitude,
                    request.client_position.longitude,
                    request.taxi_initial_position.latitude,
                    request.taxi_initial_position.longitude
                ))

                conn.commit()
                logger.info(f"✅ Service created successfully with ID: {request.service_id}")
                return taxi_service_pb2.CreateServiceResponse(
                    success=True,
                    service_id=request.service_id,
                    message="Service created successfully"
                )

        except Exception as e:
            if conn:
                conn.rollback()
            logger.error(f"❌ Error creating service: {str(e)}")
            return taxi_service_pb2.CreateServiceResponse(
                success=False,
                message=f"Error creating service: {str(e)}"
            )
        finally:
            if conn:
                self.return_connection(conn)

    def UpdateService(self, request, context):
        logger.info(f"🔄 Updating service {request.service_id}")
        conn = None
        try:
            conn = self.get_connection()
            with conn.cursor() as cur:
                # Explicitly roll back any existing transaction
                conn.rollback()

                # Update services table
                cur.execute("""
                    UPDATE services
                    SET status = %s,
                        completion_timestamp = %s
                    WHERE service_id = %s
                    RETURNING service_id, status;
                """, (
                    request.status,
                    datetime.fromisoformat(
                        request.completion_timestamp) if request.completion_timestamp else datetime.now(),
                    request.service_id
                ))

                # Check if service was updated
                if cur.rowcount == 0:
                    logger.warning(f"⚠️ Service {request.service_id} not found")
                    return taxi_service_pb2.UpdateServiceResponse(
                        success=False,
                        message="Service not found",
                        service_id=request.service_id
                    )

                # Fetch the updated status
                result = cur.fetchone()
                updated_status = result[1]

                # Update global statistics
                if updated_status == 'COMPLETED':
                    cur.execute("""
                        UPDATE service_statistics
                        SET total_services_completed = total_services_completed + 1,
                            last_update = CURRENT_TIMESTAMP
                        WHERE stat_id IS NOT NULL
                    """)
                elif updated_status == 'DENIED':
                    cur.execute("""
                        UPDATE service_statistics
                        SET total_services_denied = total_services_denied + 1,
                            last_update = CURRENT_TIMESTAMP
                        WHERE stat_id IS NOT NULL
                    """)

                conn.commit()
                logger.info(f"✅ Service {request.service_id} updated successfully")
                return taxi_service_pb2.UpdateServiceResponse(
                    success=True,
                    message="Service updated successfully",
                    service_id=request.service_id
                )

        except Exception as e:
            if conn:
                conn.rollback()
            logger.error(f"❌ Error updating service: {str(e)}")
            return taxi_service_pb2.UpdateServiceResponse(
                success=False,
                message=f"Error updating service: {str(e)}",
                service_id=request.service_id
            )
        finally:
            if conn:
                self.return_connection(conn)

    def UpdateTaxiStats(self, request, context):
        logger.info(f"📊 Updating stats for taxi {request.taxi_id}")
        conn = None
        try:
            conn = self.get_connection()
            with conn.cursor() as cur:
                # Verificar si el taxi existe
                cur.execute("SELECT taxi_id FROM taxis WHERE taxi_id = %s", (request.taxi_id,))
                if not cur.fetchone():
                    logger.warning(f"⚠️ Taxi {request.taxi_id} not found")
                    return taxi_service_pb2.UpdateTaxiStatsResponse(
                        success=False,
                        message="Taxi not found"
                    )

                # Construir la consulta SQL basada en qué contador incrementar
                update_query = """
                    UPDATE taxis 
                    SET """

                if request.increment_total:
                    update_query += "total_services = total_services + 1"
                elif request.increment_successful:
                    update_query += "successful_services = successful_services + 1"

                update_query += """
                    WHERE taxi_id = %s
                    RETURNING total_services, successful_services"""

                cur.execute(update_query, (request.taxi_id,))
                total_services, successful_services = cur.fetchone()

                conn.commit()
                logger.info(
                    f"✅ Stats updated for taxi {request.taxi_id}: total={total_services}, successful={successful_services}")

                return taxi_service_pb2.UpdateTaxiStatsResponse(
                    success=True,
                    message="Stats updated successfully",
                    total_services=total_services,
                    successful_services=successful_services
                )

        except Exception as e:
            if conn:
                conn.rollback()
            logger.error(f"❌ Error updating taxi stats: {str(e)}")
            return taxi_service_pb2.UpdateTaxiStatsResponse(
                success=False,
                message=f"Error updating taxi stats: {str(e)}"
            )
        finally:
            if conn:
                self.return_connection(conn)

    def GetAvailableTaxis(self, request, context):
            logger.info("🔍 Fetching available taxis...")
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

                    results = cur.fetchall()
                    logger.info(f"📊 Found {len(results)} available taxis")

                    for row in results:
                        taxi_id, status, total_services, successful_services, lat, lng, timestamp = row
                        logger.debug(f"🚖 Available taxi: {taxi_id} at ({lat}, {lng})")
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
                logger.error(f"❌ Error fetching available taxis: {str(e)}")
                context.set_code(grpc.StatusCode.INTERNAL)
                context.set_details(f"Error getting available taxis: {str(e)}")
            finally:
                if conn:
                    self.return_connection(conn)


def serve():
    try:
        logger.info("🌟 Starting Taxi Database Manager service...")
        server = grpc.server(futures.ThreadPoolExecutor(max_workers=10))
        taxi_service_pb2_grpc.add_TaxiDatabaseServiceServicer_to_server(
            DatabaseManager(), server)
        server.add_insecure_port('[::]:50052')
        server.start()
        logger.info("✨ DB manager started successfully on port 50052")
        server.wait_for_termination()

    except KeyboardInterrupt:
        server.stop(0)
        logger.info("👋 DB manager stopped gracefully by user")


if __name__ == '__main__':
    serve()
