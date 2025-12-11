from __future__ import annotations

import asyncio
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from typing import Dict, List, Set

from fastapi import WebSocket

from app.config import (
    PLC_CONFIG,
    BROADCAST_INTERVAL_SECONDS,
    PLC_RECONNECT_DELAY_MINUTES,
    COMMON_ROOT_NODE_ID,
)
from app.opcua_client import OpcUaClient, ConnectionStatus
from app.telemetry import payload_from_raw_list


# Global state for OPC UA + WS
stop_event = threading.Event()
plc_clients: List[OpcUaClient] = []
active_ws_connections: Dict[str, Set[WebSocket]] = {}

# Thread pool used for OPC UA connect/read
executor = ThreadPoolExecutor(max_workers=max(len(PLC_CONFIG) * 2, 2))


def init_plc_clients() -> List[OpcUaClient]:
    """
    Initialize global PLC clients list from PLC_CONFIG.
    Called once at startup.
    """
    global plc_clients
    plc_clients = [
        OpcUaClient(cfg["url"], cfg["name"], COMMON_ROOT_NODE_ID)
        for cfg in PLC_CONFIG
    ]
    logging.info(f"Initialized {len(plc_clients)} OPC UA clients")
    return plc_clients


def get_plc_clients() -> List[OpcUaClient]:
    """
    Accessor so other modules don't rely directly on the global name.
    """
    return plc_clients


def disconnect_all_clients() -> None:
    """
    Cleanly disconnect all OPC UA clients (used on shutdown).
    Uses disconnect_safe to avoid long blocking disconnects.
    """
    for cli in list(plc_clients):
        try:
            cli.disconnect_safe()
        except Exception as e:
            logging.warning(f"Error disconnecting {cli.name}: {e}")


def data_broadcast_loop(loop: asyncio.AbstractEventLoop) -> None:
    """
    Background thread:
    - Reconnects dropped PLCs after a delay
    - Reads all PLC data
    - Broadcasts telemetry to all active WebSockets
    - Exits quickly when stop_event is set or the event loop closes
    """
    reconnect_delay = timedelta(minutes=PLC_RECONNECT_DELAY_MINUTES)
    logging.info("Background broadcast started.")

    while not stop_event.is_set():
        # If the event loop is already closed, bail out quickly
        if loop.is_closed():
            logging.info("Event loop is closed; stopping broadcast thread.")
            break

        try:
            # Reconnect any dropped/error clients with backoff
            reconnect_list = [
                p
                for p in plc_clients
                if p.status in (ConnectionStatus.DISCONNECTED, ConnectionStatus.ERROR)
                and (
                    not p.last_reconnect_attempt
                    or (datetime.now() - p.last_reconnect_attempt) > reconnect_delay
                )
            ]

            if reconnect_list:
                list(executor.map(lambda p: p.connect_and_discover(), reconnect_list))

            # Read all PLC data once per tick
            all_plc_data = list(executor.map(lambda p: p.read_data(), plc_clients))

            # Broadcast filtered data to each websocket
            for user_id, sockets in list(active_ws_connections.items()):
                for ws in list(sockets):
                    try:
                        if loop.is_closed():
                            raise RuntimeError("Event loop closed")

                        allowed = getattr(ws, "allowed_urls", None)
                        visible_raw = (
                            [d for d in all_plc_data if d.get("url") in allowed]
                            if allowed
                            else all_plc_data
                        )

                        payload = {
                            "type": "telemetry_update",
                            "data": payload_from_raw_list(visible_raw),
                        }

                        asyncio.run_coroutine_threadsafe(ws.send_json(payload), loop)

                    except RuntimeError as e:
                        # Happens when trying to submit to a closed loop during shutdown
                        logging.info(f"Stopping broadcast send loop for {user_id}: {e}")
                        break
                    except Exception as e:
                        logging.error(f"WebSocket send error for {user_id}: {e}")

            # Sleep, but wake early if stop_event is set
            sleep_secs = int(BROADCAST_INTERVAL_SECONDS) or 1
            for _ in range(sleep_secs):
                if stop_event.wait(1):
                    break

        except Exception as e:
            logging.error(f"Broadcast error: {e}")
            # Wait a bit, but allow immediate shutdown via stop_event
            if stop_event.wait(5):
                break

    logging.info("Broadcast stopped.")
