import psycopg2
import logging


def test_db_connection():
    try:
        # Configuración de logging
        logging.basicConfig(level=logging.INFO)

        # Conectar a la base de datos
        conn = psycopg2.connect(
            host='droll-rabbit-5432.7tt.aws-us-east-1.cockroachlabs.cloud',
            port=26257,
            database='my_uber',
            user='nidhood',
            password='KUukuDVmnSeSOB411JLJwg',
            sslmode='verify-full',
            sslrootcert='certificate/root.crt'
        )

        # Crear las tablas necesarias si no existen
        with conn.cursor() as cur:
            # Tabla de taxis
            cur.execute("""
                CREATE TABLE IF NOT EXISTS taxis (
                    taxi_id UUID PRIMARY KEY,
                    status TEXT NOT NULL,
                    total_services INT DEFAULT 0,
                    successful_services INT DEFAULT 0,
                    last_update TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # Tabla de posiciones de taxis
            cur.execute("""
                CREATE TABLE IF NOT EXISTS taxi_locations (
                    id SERIAL PRIMARY KEY,
                    taxi_id UUID REFERENCES taxis(taxi_id),
                    latitude FLOAT NOT NULL,
                    longitude FLOAT NOT NULL,
                    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # Tabla de servicios
            cur.execute("""
                CREATE TABLE IF NOT EXISTS services (
                    service_id UUID PRIMARY KEY,
                    taxi_id UUID REFERENCES taxis(taxi_id),
                    client_id UUID NOT NULL,
                    status TEXT NOT NULL,
                    start_position_lat FLOAT,
                    start_position_lng FLOAT,
                    end_position_lat FLOAT,
                    end_position_lng FLOAT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            conn.commit()
            logging.info("Tablas creadas exitosamente")

            # Probar la inserción de datos
            cur.execute("""
                INSERT INTO taxis (taxi_id, status)
                VALUES ('11111111-1111-1111-1111-111111111111', 'AVAILABLE')
                ON CONFLICT (taxi_id) DO NOTHING
            """)
            conn.commit()

            # Verificar los datos
            cur.execute("SELECT * FROM taxis LIMIT 1")
            result = cur.fetchone()
            logging.info(f"Datos de prueba: {result}")

        logging.info("Conexión a la base de datos exitosa y pruebas completadas")
        return True

    except Exception as e:
        logging.error(f"Error al conectar con la base de datos: {e}")
        return False
    finally:
        if 'conn' in locals():
            conn.close()


if __name__ == "__main__":
    test_db_connection()