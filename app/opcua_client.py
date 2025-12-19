from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Dict, Optional
import concurrent.futures
import logging
import threading

from opcua import ua, Client
from opcua.ua.uaerrors import UaStatusCodeError

from app.config import TIMEOUT_CONNECT, TIMEOUT_METADATA, TIMEOUT_DISCOVERY


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
    """
    Thread-safe-ish wrapper around python-opcua Client.
    Public API expected by the rest of the project:
      - connect_and_discover()
      - read_data()
      - disconnect_safe()
    """

    def __init__(self, url: str, custom_name: str, root_node_id: str):
        self.url = url
        self.name = custom_name
        self.server_name = ""
        self.root_node_id = root_node_id

        self.client: Optional[Client] = None
        self.nodes: Dict[str, Any] = {}

        self.status = ConnectionStatus.DISCONNECTED
        self.last_reconnect_attempt: Optional[datetime] = None

        # Prevent read/write/connect colliding on the same underlying socket.
        self._lock = threading.RLock()

    @property
    def lock(self) -> threading.RLock:
        return self._lock

    # ----------------------------------------
    # SAFE DISCONNECT (fast shutdown)
    # ----------------------------------------
    def disconnect_safe(self) -> None:
        with self._lock:
            try:
                if self.client:
                    # Best-effort: close underlying socket/websocket
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

                    # Avoid blocking .disconnect()
                    self.client = None
            except Exception:
                pass

            self.status = ConnectionStatus.DISCONNECTED

    # ----------------------------------------
    # NODE DISCOVERY
    # ----------------------------------------
    def _get_readable_nodes(self, node) -> Dict[str, Any]:
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

    def _discover_nodes(self) -> Dict[str, Any]:
        root = self.client.get_node(self.root_node_id)  # type: ignore[union-attr]
        return self._get_readable_nodes(root)

    # ----------------------------------------
    # CONNECT + DISCOVER (timeouts protected)
    # ----------------------------------------
    def _perform_connect(self) -> None:
        self.client.connect()  # type: ignore[union-attr]

    def connect_and_discover(self) -> bool:
        with self._lock:
            self.last_reconnect_attempt = datetime.now()

            # clean old client best-effort
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

                # METADATA TIMEOUT (optional)
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
        with self._lock:
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
