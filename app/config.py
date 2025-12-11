from __future__ import annotations

import json
import os
from pathlib import Path
from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parent.parent  # adjust if main moves

load_dotenv(dotenv_path=ROOT_DIR / ".env")

with open(ROOT_DIR / "config.json", "r", encoding="utf-8") as f:
    config = json.load(f)

PLC_CONFIG = config["plc_config"]
COMMON_ROOT_NODE_ID = config["common_root_node_id"]
BROADCAST_INTERVAL_SECONDS = float(config["broadcast_interval_seconds"])
PLC_RECONNECT_DELAY_MINUTES = int(config["plc_reconnect_delay_minutes"])

CORS_ALLOW_ORIGINS = os.getenv("CORS_ALLOW_ORIGINS", "")
SECRET_KEY = os.getenv("SECRET_KEY") or "change-me"
