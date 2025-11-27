# SCADA Backend

Backend service for a SCADA-style monitoring system.
Communicates with PLCs over OPC UA, streams real-time telemetry, and exposes a REST + WebSocket API for the React frontend.

---

## Features

### 🔌 OPC UA Integration
- Connects to multiple PLCs (configured in `config.json`)
- Discovers readable nodes automatically
- Periodically polls values and maintains a live snapshot

### 📡 Real-time Telemetry
- `GET /data` → initial data for dashboard  
- `WebSocket /ws` → live updates for allowed parks

### 🔐 Authentication & Users
- Email/password login via FastAPI Users
- JWT authentication (used for REST + WebSockets)
- Active/superuser flags
- Users can be restricted to specific parks

### 🏭 Park Access Control
- Superusers see all parks
- Normal users only see parks assigned to them

### 🛠 PLC Write Commands
- Controlled `POST /write_value` endpoint
- Supports setpoints + instant cutoff command
- Permission-restricted

---

## Tech Stack

- **Python 3.11+**
- **FastAPI**
- **fastapi-users** (authentication)
- **SQLAlchemy (async)**
- **python-opcua**
- **Uvicorn**

---

## Project Structure

```text
SCADA-Backend/
│
├── main.py                 # Application entrypoint
├── config.json             # PLC list + OPC root node + interval settings
├── auth.py                 # Auth system (FastAPI Users)
├── models_user.py          # User model (SQLAlchemy)
├── schemas_user.py         # User schemas (Pydantic)
├── models_user_park.py     # User↔Park access model
├── parks.py                # Park definitions
├── parks_routes.py         # Park admin routes
├── db_async.py             # Database session factory
├── requirements.txt        # Python dependencies
└── (optional helper scripts)
```

---

## Prerequisites

- Python **3.11+**
- PostgreSQL (or any DB supported by SQLAlchemy)
- Accessible OPC UA devices

---

## Setup Instructions (Copy & Paste Ready)

All steps assume you are **already inside the project folder**.

---

### **1. Create and activate a virtual environment**

```bash
python -m venv .venv
```

Windows PowerShell:
```bash
. .venv/Scripts/Activate.ps1
```

Linux / macOS:
```bash
source .venv/bin/activate
```

---

### **2. Install dependencies**

```bash
pip install --upgrade pip
pip install -r requirements.txt
```

---

### **3. Configure environment variables**

Create a `.env` file:

```bash
DATABASE_URL=postgresql+asyncpg://user:password@localhost:5432/scada
SECRET_KEY=replace-with-long-random-string
CORS_ALLOW_ORIGINS=http://localhost:5173
```

---

### **4. Configure your PLCs**

Edit `config.json`:

```bash
{
  "plc_config": [
    {
      "name": "Eco Solar",
      "url": "opc.tcp://192.168.41.230:4840"
    }
  ],
  "common_root_node_id": "ns=4;i=67",
  "broadcast_interval_seconds": 5,
  "plc_reconnect_delay_minutes": 5
}
```

---

### **5. Initialize the database**

```bash
python init_db_async.py
```

---

### **6. Run the backend**

Development:

```bash
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

Production:

```bash
uvicorn main:app --host 0.0.0.0 --port 8000
```

API docs:

- http://localhost:8000/docs  
- http://localhost:8000/redoc

---

## Important API Endpoints

```bash
POST    /auth/jwt/login      Login → JWT token
GET     /me                  Current user
GET     /data                Telemetry snapshot
POST    /write_value         Write value to PLC
GET     /admin/users         User management
GET     /admin/parks         Park management
WS      /ws                  Live telemetry
```

---

## Notes

```
Keep config.json and .env out of version control if they contain secrets.
Ensure proper CORS when deploying.
Ensure SECRET_KEY is secure in production.
```

# END OF README
