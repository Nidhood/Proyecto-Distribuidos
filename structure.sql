-- Tabla de taxis
CREATE TABLE taxis (
    taxi_id UUID PRIMARY KEY,
    status TEXT NOT NULL CHECK (status IN ('AVAILABLE', 'BUSY', 'OFFLINE')),
    registration_date TIMESTAMP DEFAULT current_timestamp(),
    total_services INT DEFAULT 0,
    successful_services INT DEFAULT 0,
    last_update TIMESTAMP
);

-- Tabla para tracking de posiciones de taxis
CREATE TABLE taxi_locations (
    location_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    taxi_id UUID NOT NULL REFERENCES taxis(taxi_id),
    latitude DECIMAL NOT NULL,
    longitude DECIMAL NOT NULL,
    timestamp TIMESTAMP DEFAULT current_timestamp(),
    INDEX idx_taxi_locations (taxi_id, timestamp DESC)
);

-- Tabla de servicios
CREATE TABLE services (
    service_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    taxi_id UUID REFERENCES taxis(taxi_id),
    client_id UUID NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('REQUESTED', 'ASSIGNED', 'COMPLETED', 'CANCELLED', 'DENIED')),
    request_timestamp TIMESTAMP DEFAULT current_timestamp(),
    completion_timestamp TIMESTAMP,

    -- Posición del cliente
    client_latitude DECIMAL NOT NULL,
    client_longitude DECIMAL NOT NULL,

    -- Posición del taxi al momento de la asignación
    taxi_initial_latitude DECIMAL,
    taxi_initial_longitude DECIMAL,

    INDEX idx_taxi_services (taxi_id, request_timestamp DESC),
    INDEX idx_service_status (status, request_timestamp DESC)
);

-- Tabla de estadísticas globales
CREATE TABLE service_statistics (
    stat_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    request_time TIMESTAMP DEFAULT current_timestamp(),
    response_time TIMESTAMP,
    status VARCHAR(50) CHECK (status IN ('accepted', 'denied', 'timeout')),
    response_duration INTERVAL,
    user_id UUID,
    taxi_id UUID
);

-- Índices adicionales para optimizar consultas comunes
CREATE INDEX idx_taxi_status ON taxis(status);
CREATE INDEX idx_service_timestamps ON services(request_timestamp, completion_timestamp);
CREATE INDEX idx_taxi_locations_timestamp ON taxi_locations(timestamp DESC);
