# SCADA Backend

Backend service for a SCADA-style monitoring platform.  
It talks to Siemens S7-1200 PLCs over OPC UA, streams real-time telemetry,  
and exposes a REST + WebSocket API for the React frontend.

The backend handles:

- OPC UA connections to multiple parks
- High-frequency telemetry polling + broadcasting
- Authentication & authorization (FastAPI Users)
- Park-based access control
- Safe PLC write commands

---

## 🚀 Features

### 🔌 OPC UA Integration

- Connects to **multiple PLCs**, defined in `config.json`
- Automatically discovers readable nodes under a **common root node**
- Rebuilds the node map fresh on every reconnection
- Reconnect strategy with configurable delay

### 📡 Real-time Telemetry

- `GET /data` → initial snapshot for the dashboard  
- `WebSocket /ws` → continuous updates, pushed on every broadcast tick  
- Each user only receives data for **parks they are allowed to see**

### 🔐 Authentication & Users

- Email/password login using **fastapi-users**
- JWT-based authentication for REST and WebSockets
- Active / superuser flags
- User ↔ Park access via `UserParkAccess` table

### 🏭 Park Access Control

- Superusers: see **all** configured parks
- Normal users: see only parks assigned to them (per DB)

### 🛠 PLC Write Commands

- `POST /write_value` endpoint
- Type-safe writes using OPC UA variant types
- Permission-checked per PLC URL
- Special array-write support for `CMD_Instant_Cutoff`  
  (maps to individual bit nodes `[0]`, `[1]` etc.)

---

## 🧠 Architecture Overview

### 1. OPC UA Client Layer (`app/opcua_client.py`)

Each PLC has an `OpcUaClient` instance.

- `connect_and_discover()`  
  Connects, reads server name, builds node map under `COMMON_ROOT_NODE_ID`.
- `read_data()`  
  Bulk-reads all discovered nodes and returns a simple dictionary.
- `disconnect_safe()`  
  Fast, non-blocking disconnect that avoids long hangs on some PLCs.

State machine:

```text
DISCONNECTED → CONNECTING → CONNECTED → ERROR → (reconnect scheduled)
```

---

### 2. Broadcast Engine (`app/broadcast.py`)

Runs in a **separate background thread**.

Responsibilities:

- Maintains a global list of `plc_clients`
- Periodically polls each PLC in a `ThreadPoolExecutor`
- Handles reconnects based on `PLC_RECONNECT_DELAY_MINUTES`
- Sends filtered telemetry to each WebSocket connection
- Stops cleanly when the FastAPI app shuts down

Key elements:

- `stop_event` – tells the loop to exit
- `plc_clients` – global list of `OpcUaClient`
- `active_ws_connections` – `user_id → set[WebSocket]`
- `executor` – thread pool used for PLC reads

---

### 3. Telemetry Formatting (`app/telemetry.py`)

Converts raw PLC data into a frontend-friendly shape:

```jsonc
{
  "plc_clients": [
    {
      "name": "Eco Solar",
      "url": "opc.tcp://192.168.41.230:4840",
      "status": "CONNECTED",
      "nodes": [
        { "name": "Active_Power_kW", "value": "123.4" },
        ...
      ]
    }
  ]
}
```

Used by:

- `GET /data`
- WebSocket `/ws` broadcast payloads

---

### 4. WebSockets (`app/routes/ws.py`)

`/ws` endpoint:

1. Accepts connection with JWT in either:
   - `Sec-WebSocket-Protocol: bearer,<JWT>`  
   - Query parameter `?token=<JWT>`
2. Validates the token and user activity.
3. Computes allowed park URLs for this user.
4. Registers the socket into `active_ws_connections[user_id]`.
5. Broadcast thread pushes `telemetry_update` messages to this socket.

On disconnect or cancellation, the socket is removed from the map.

---

### 5. REST API Routers (`app/routes/*.py`)

- `auth_extra.py` – extra auth-related endpoints (e.g. `/auth/register` if present)
- `admin.py` – admin-only endpoints (user list, etc.)
- `me.py` – current user info (`/me`)
- `data.py` – telemetry snapshot (`/data`)
- `write.py` – PLC write endpoint (`/write_value`)
- `ws.py` – WebSocket endpoint (`/ws`)

These routers are included from `main.py`.

---

## 🧱 Tech Stack

- **Python 3.11+**
- **FastAPI**
- **fastapi-users**
- **SQLAlchemy (async)**
- **python-opcua**
- **asyncpg**
- **Uvicorn**

---

## 📁 Project Structure (Current)

```text
OPCUA_PROJECT/
│
├── .env
├── auth.py
├── config.json              # PLC list, root node, timings
├── db_async.py
├── init_db_async.py
├── main.py                  # FastAPI entrypoint
├── models_user.py
├── models_user_park.py
├── parks.py                 # Park definitions
├── parks_routes.py          # Park routes
├── promote_superuser.py     # Helper script
├── README.md
├── requirements.txt
├── schemas_user.py
│
├── app/
│   ├── __init__.py
│   ├── auth_helpers.py
│   ├── broadcast.py         # Background loop + PLC client globals
│   ├── config.py            # Loads config.json + .env
│   ├── logging_config.py
│   ├── opcua_client.py
│   ├── schemas.py           # Extra Pydantic schemas (e.g. WriteRequest)
│   ├── telemetry.py
│   │
│   └── routes/
│       ├── __init__.py
│       ├── admin.py
│       ├── auth_extra.py
│       ├── data.py
│       ├── me.py
│       ├── write.py
│       └── ws.py
│
├── certs/                   # OPC UA certificates / keys
└── dev_tools/               # Extra scripts/tools (if any)
```

---

## 🔧 Setup Instructions (Copy & Paste)

All commands assume you are already inside the project folder.

### 1. Create & activate virtual environment

```bash
python -m venv .venv
```

Windows PowerShell:

```bash
. .venv/Scripts/Activate.ps1
```

Linux/macOS:

```bash
source .venv/bin/activate
```

---

### 2. Install dependencies

```bash
pip install --upgrade pip
pip install -r requirements.txt
```

---

### 3. Configure environment variables (`.env`)

Example:

```env
DATABASE_URL=postgresql+asyncpg://user:password@localhost:5433/scada
SECRET_KEY=replace-with-long-random-string
CORS_ALLOW_ORIGINS=http://localhost:5173,http://127.0.0.1:5173
```

> Make sure `DATABASE_URL` matches your local Postgres setup (port, user, db name).

---

### 4. Configure PLCs (`config.json`)

Example:

```json
{
  "plc_config": [
    { "id": "eco_solar",   "url": "opc.tcp://192.168.41.230:4840", "name": "Eco Solar" },
    { "id": "isiada",      "url": "opc.tcp://192.168.42.230:4840", "name": "Isiada"      }
  ],
  "common_root_node_id": "ns=4;i=2",
  "broadcast_interval_seconds": 2.0,
  "plc_reconnect_delay_minutes": 10
}
```

---

### 5. Initialize the database

```bash
python init_db_async.py
```

---

### 6. Run the backend

Development:

```bash
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

Production (basic):

```bash
uvicorn main:app --host 0.0.0.0 --port 8000
```

API docs:

- Swagger UI → http://localhost:8000/docs  
- ReDoc → http://localhost:8000/redoc  

---

## 🌐 Key API Endpoints

```text
POST   /auth/jwt/login        → Login (JWT)
POST   /auth/register         → (if enabled) Register new user
GET    /me                    → Current user
GET    /data                  → Telemetry snapshot
POST   /write_value           → Write command to PLC
GET    /admin/users           → Admin: list users
GET    /admin/parks           → Admin: park list
WS     /ws                    → Live telemetry WebSocket
```

WebSocket messages (example):

```json
{
  "type": "telemetry_update",
  "data": {
    "plc_clients": [
      {
        "name": "Eco Solar",
        "url": "opc.tcp://192.168.41.230:4840",
        "status": "CONNECTED",
        "nodes": [
          { "name": "Active_Power_kW", "value": "123.4" }
        ]
      }
    ]
  }
}
```

---

## ⚠️ Deployment Notes

- Do **not** commit `.env` or production `config.json` to Git if they contain secrets.
- Use a long, random `SECRET_KEY` in production.
- Set `CORS_ALLOW_ORIGINS` to the real frontend URL(s).
- Make sure the server can reach all PLC IPs/ports.
- Use `disconnect_safe()` during shutdown to avoid blocking PLC disconnects.