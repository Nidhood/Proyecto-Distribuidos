Aquí tienes una versión mejorada de tu documento para MyUber, con un enfoque más claro, uso de emojis y una estructura organizada que facilita la lectura. Además, he incluido el diagrama en Mermaid para que puedas visualizar la arquitectura del sistema de manera efectiva.

---

# 🚖 MyUber - Sistema Distribuido de Gestión de Taxis

## 📝 Descripción
**MyUber** es un sistema distribuido para la gestión de servicios de taxi que utiliza una arquitectura basada en microservicios, implementando **gRPC** y **ZeroMQ**. Este sistema permite la comunicación en tiempo real entre taxis y clientes, gestión de ubicaciones, asignación de servicios y seguimiento de métricas.

## 🏗️ Arquitectura del Sistema

### 🔑 Componentes Principales
1. **Servidor Central (`servidor.py`)**
   - Gestiona la comunicación entre clientes y taxis.
   - Implementa servicios gRPC para la gestión de datos.
   - Maneja la lógica de asignación de servicios.

2. **Gestor de Base de Datos (`gestor_db.py`)**
   - Maneja todas las operaciones de persistencia.
   - Almacena información de taxis, servicios y ubicaciones.
   - Proporciona estadísticas del sistema.

3. **Broker de Mensajería (`broker.py`)**
   - Implementa el patrón publicador/suscriptor con ZeroMQ.
   - Gestiona la comunicación asíncrona entre componentes.
   - Incluye un broker de respaldo para alta disponibilidad.

4. **Cliente (`cliente.py`)**
   - Interfaz para solicitar servicios de taxi.
   - Se comunica a través del broker de mensajería.
   - Recibe actualizaciones en tiempo real.

5. **Taxi (`taxi.py`)**
   - Gestiona el estado y ubicación de cada taxi.
   - Recibe y procesa asignaciones de servicios.
   - Actualiza su posición periódicamente.

### 📊 Diagrama de Arquitectura
```mermaid
---
config:
  theme: base
  themeVariables:
    fontSize: 16px
    nodeSpacing: '70'
    rankSpacing: '70'
---
flowchart LR
 subgraph Clientes["Clientes"]
    direction TB
        A1("<h2>Cliente</h2>IP: Varias direcciones")
  end
 subgraph Taxis["Taxis"]
    direction TB
        C("<h2>Taxi</h2>IP: Varias direcciones")
  end
 subgraph subGraph2["Servidores"]
        E("<h2>Servidor de Respaldo</h2><h5>IP: 127.0.0.1:5003</h5><h5>Replica del Servidor Central</h5>")
            B(("<h2>Servidor Central</h2><h5>IP: 127.0.0.1:5000</h5>"))
  end
 subgraph subGraph3["Health Check"]
        F("<h2>Health Check</h2>")
  end
 subgraph s1["Bases de Datos"]
        D("<h2>Base de Datos (Servidor Central)</h2>")
        D2("<h2>Base de Datos (Servidor de Respaldo)</h2>")
  end
    A1 -- <h4>1. ZeroMQ:</h4>SolicitudServicio --> B
    B -- <h4>ZeroMQ</h4> --> C
    B -- <h4>ZeroMQ:</h4>AsignarTaxi --> C
    C -- <h4>ZeroMQ:</h4>ConfirmaciónServicio --> B
    B -- <h4>ZeroMQ:</h4>ResultadoServicio --> A1
    B -- <h4>gRPC:</h4>GuardarEnDB --> D
    B -. Replicación .-> E
    E -- <h4>gRPC:</h4>Conexión con Base de Datos --> D2
    A1 -- <h4>Timeout</h4> --> E
    E -- <h4>Detecta falla en el Servidor Central</h4> --> B
    E -- <h4>gRPC:</h4>GuardarEnDB --> D2
    D -. <h4>Replicación</h4> .-> D2
    F -- <h4>Monitoreo</h4> --> B & E & D & D2
     D2:::bd
     F:::hc
     D:::bd
    classDef bd fill:#f9f,stroke:#333,stroke-width:2px
    classDef hc fill:#bbf,stroke:#333,stroke-width:2px
```
### 🌐 Diagrama de Secuencia
```mermaid
sequenceDiagram
    participant C as Cliente
    participant SC as Servidor Central
    participant T as Taxi
    participant DB as Base de Datos
    participant SR as Servidor de Respaldo
    participant HC as Health Check

    rect rgb(200, 255, 200)
        Note over C,HC: Operaciones normales
        C->>+SC: 1. ServicioTaxi.Solicitar (ZeroMQ pub/sub)
        SC->>DB: 2. Consultar taxis disponibles
        activate DB
        DB-->>SC: 3. Lista de taxis disponibles
        deactivate DB
        SC->>SC: 4. Calcular taxi más cercano
        SC->>+T: 5. gRPC call: ServicioTaxi.Asignar(TaxiCercano)
        T-->>-SC: 6. gRPC reply: ConfirmaciónServicio
        SC->>DB: 7. Guardar asignación de servicio
        activate DB
         deactivate DB
        SC-->>-C: 8. ResultadoServicio (ZeroMQ pub/sub)
       
    end

    rect rgb(200, 200, 255)
        Note over T,DB: Actualización de posiciones
        loop Cada 5 segundos
            T->>SC: 9. Publicar posición (ZeroMQ pub/sub)
            activate T
            deactivate T
            activate SC
            SC->>DB: 10. gRPC call: Actualizar posición del taxi
            activate DB
            DB-->>SC: 11. gRPC reply: ConfirmaciónServicio
            deactivate DB
            deactivate SC
        end
    end

    rect rgb(255, 255, 200)
        Note over SC,HC: Health Check
        par Replicación continua cada 5 min
            SC->>SR: 12. Replicar datos
            activate SR
            deactivate SR
            activate SC
        and Health Check
    
            HC->>SC: 13. Verificar estado
            activate HC
            SC-->>HC: 14. Estado OK
            deactivate HC
            deactivate SC
        end
    end

    rect rgb(255, 200, 200)
        Note over SC,SR: Manejo de fallas
        alt Falla del Servidor Central
            
            HC-xSC: 15. No responde
            activate SC
            deactivate SC
            activate HC
            HC->>SR: 16. Activar Servidor de Respaldo
            deactivate HC
            Note over SR: Asume funciones del Servidor Central
        end
    end
```

## 📦 Dependencias

### 🖥️ Requisitos del Sistema
- Python 3.8 o superior
- CockroachDB (para la base de datos)

### 📚 Bibliotecas Python
```bash
# Instalar dependencias
pip install grpcio==1.54.2
pip install grpcio-tools==1.54.2
pip install psycopg2-binary==2.9.6
pip install pyzmq==25.1.1
pip install protobuf==4.23.1
```

## ⚙️ Configuración

### 1. Base de Datos
1. Crear una base de datos en CockroachDB.
2. Ejecutar el script de inicialización:

```sql
CREATE TABLE taxis (
    taxi_id UUID PRIMARY KEY,
    status TEXT,
    last_update TIMESTAMP,
    total_services INT DEFAULT 0,
    successful_services INT DEFAULT 0
);

CREATE TABLE taxi_locations (
    location_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    taxi_id UUID REFERENCES taxis(taxi_id),
    latitude FLOAT,
    longitude FLOAT,
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE services (
    service_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    client_id UUID,
    taxi_id UUID REFERENCES taxis(taxi_id),
    status TEXT,
    request_timestamp TIMESTAMP,
    completion_timestamp TIMESTAMP,
    client_latitude FLOAT,
    client_longitude FLOAT,
    taxi_latitude FLOAT,
    taxi_longitude FLOAT
);
```

### 2. Configuración de Servicios
1. Actualizar las credenciales de la base de datos en `gestor_db.py`.
2. Configurar los puertos en `broker.py`.
3. Ajustar las direcciones IP/puertos en `servidor.py`.

## ▶️ Ejecución

### 1. Iniciar los Servicios Base
```bash
# Iniciar el Gestor de Base de Datos
python gestor_db.py

# Iniciar el Broker de Mensajería
python broker.py

# Iniciar el Servidor
python servidor_principal.py
```

### 2. Iniciar Clientes y Taxis
```bash
# Iniciar un nuevo taxi
python taxi.py

# Iniciar un nuevo cliente
python usuario.py
```

## 📈 Monitoreo y Estadísticas

El sistema proporciona estadísticas en tiempo real sobre:
- 📊 Número total de servicios
- ✅ Servicios completados y denegados
- 📍 Posiciones históricas de taxis
- 📊 Métricas por taxi
- 🔍 Estado del sistema

Para acceder a las estadísticas, utilizar el método `GetStatistics` del servicio gRPC.

## 🧪 Pruebas
Para ejecutar las pruebas del sistema:
```bash
python -m unittest discover tests
```

## 🔒 Consideraciones de Seguridad
- Las credenciales de la base de datos deben ser manejadas como variables de entorno.
- Implementar autenticación para las comunicaciones gRPC.
- Usar SSL/TLS para las conexiones.
- Validar y sanitizar todas las entradas de usuario.

## 🤝 Contribución
1. Fork del repositorio.
2. Crear una rama para la nueva característica.
3. Commit de los cambios.
4. Push a la rama.
5. Crear un Pull Request.

## 📄 Licencia
Este proyecto está bajo la licencia MIT. Ver el archivo `LICENSE` para más detalles.

## 📬 Contacto
Para preguntas o sugerencias, por favor abrir un issue en el repositorio.

---

Si necesitas realizar más ajustes o incluir información adicional, ¡házmelo saber!