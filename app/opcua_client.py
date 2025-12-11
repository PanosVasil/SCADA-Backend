from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Dict, Optional

from opcua import ua, Client
from opcua.ua.uaerrors import UaStatusCodeError
import logging
import concurrent.futures

from app.config import (
    TIMEOUT_CONNECT,
    TIMEOUT_METADATA,
    TIMEOUT_DISCOVERY,
)


def run_with_timeout(func, timeout: float):
    """
    Run a blocking function in a temporary thread with a hard timeout.
    Returns the function result or raises TimeoutError.
    """
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as exe:
        future = exe.submit(func)
        try:
            return future.result(timeout=timeout)
        except concurrent.futures.TimeoutError:
            raise TimeoutError(f"Operation timed out after {timeout} seconds")


class ConnectionStatus(str, Enum):
    CONNECTED = "CONNECTED"
    CONNECTING = "CONNECTING"
    DISCONNECTED = "DISCONNECTED"
    ERROR = "ERROR"


class OpcUaClient:
    def __init__(self, url: str, custom_name: str, root_node_id: str):
        self.url = url
        self.name = custom_name
        self.server_name = ""
        self.root_node_id = root_node_id
        self.client: Optional[Client] = None
        self.nodes: Dict[str, Any] = {}
        self.status = ConnectionStatus.DISCONNECTED
        self.last_reconnect_attempt: Optional[datetime] = None

    # ----------------------------------------
    # SAFE DISCONNECT
    # ----------------------------------------
    def disconnect_safe(self):
        try:
            if self.client:
                try:
                    ua_socket = getattr(self.client.uaclient, "_uasocket", None)
                    if ua_socket:
                        ws = getattr(ua_socket, "websocket", None)
                        if ws:
                            try:
                                ws.close_connection()
                            except Exception:
                                pass
                except Exception:
                    pass

                self.client = None

        except Exception:
            pass

        self.status = ConnectionStatus.DISCONNECTED

    # ----------------------------------------
    # NODE DISCOVERY (wrapped in a timeout)
    # ----------------------------------------
    def _discover_nodes(self):
        root = self.client.get_node(self.root_node_id)
        return self._get_readable_nodes(root)

    def _get_readable_nodes(self, node) -> dict:
        nodes_dict: Dict[str, Any] = {}

        try:
            if node.get_node_class() == ua.NodeClass.Variable:
                nodes_dict[node.get_browse_name().Name] = node
        except Exception:
            pass

        try:
            for child in node.get_children():
                nodes_dict.update(self._get_readable_nodes(child))
        except Exception:
            pass

        return nodes_dict

    # ----------------------------------------
    # CONNECT + DISCOVER NODES  (ALL TIMEOUT PROTECTED)
    # ----------------------------------------
    def _perform_connect(self):
        self.client.connect()

    def connect_and_discover(self) -> bool:
        self.last_reconnect_attempt = datetime.now()

        if self.client:
            try:
                self.client.disconnect()
            except Exception:
                pass

        self.client = Client(self.url, timeout=40)
        self.status = ConnectionStatus.CONNECTING
        self.nodes = {}

        logging.info(f"{self.name}: 🔄 Connecting to {self.url} ...")

        try:
            # CONNECT TIMEOUT
            run_with_timeout(self._perform_connect, TIMEOUT_CONNECT)

            # METADATA TIMEOUT
            def read_metadata():
                try:
                    return self.client.get_node("ns=0;i=2254").get_value()
                except Exception:
                    return ""

            try:
                self.server_name = run_with_timeout(read_metadata, TIMEOUT_METADATA) or ""
            except TimeoutError:
                logging.warning(f"{self.name}: ⏳ Metadata read timed out")
                self.server_name = ""

            # DISCOVERY TIMEOUT
            try:
                self.nodes = run_with_timeout(self._discover_nodes, TIMEOUT_DISCOVERY)
            except TimeoutError:
                logging.error(f"{self.name}: ⏳ Node discovery timed out")
                self.status = ConnectionStatus.ERROR
                return False

            logging.info(f"{self.name}: 🔁 Node map built ({len(self.nodes)} nodes).")
            self.status = ConnectionStatus.CONNECTED
            logging.info(f"{self.name}: ✅ CONNECTED")
            return True

        except TimeoutError as e:
            logging.error(f"{self.name}: ⏳ Connection timeout: {e}")
            self.status = ConnectionStatus.DISCONNECTED
            self.client = None
            return False

        except Exception as e:
            logging.error(f"{self.name}: ❌ Connection error: {e}")
            self.status = ConnectionStatus.DISCONNECTED
            self.client = None
            return False

    # ----------------------------------------
    # READ DATA
    # ----------------------------------------
    def read_data(self) -> Dict[str, Any]:
        data = {
            "name": self.name,
            "status": self.status.value,
            "url": self.url,
            "nodes": {},
        }

        if self.status != ConnectionStatus.CONNECTED or not self.client:
            return data

        try:
            ids = list(self.nodes.values())
            names = list(self.nodes.keys())

            if not ids:
                data["error"] = "No readable nodes."
                return data

            values = self.client.get_values(ids)

            for n, v in zip(names, values):
                data["nodes"][n] = v

        except UaStatusCodeError as e:
            self.status = ConnectionStatus.ERROR
            data["error"] = f"OPC UA read error: {e}"
        except Exception:
            data["error"] = "Temporary read failure."

        return data
