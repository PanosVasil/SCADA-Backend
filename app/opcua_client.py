from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Dict, Optional

from opcua import ua, Client
from opcua.ua.uaerrors import UaStatusCodeError
import logging


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
    # 🔥 SAFE DISCONNECT — prevents shutdown delays
    # ----------------------------------------
    def disconnect_safe(self):
        """
        Fast, safe disconnect that avoids blocking OPC UA disconnect()
        calls that can hang for 20–40 seconds on Siemens PLCs.
        """
        try:
            if self.client:
                # Try to close the underlying websocket/socket directly
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

                # Avoid calling .disconnect(), which blocks
                self.client = None

        except Exception:
            pass

        self.status = ConnectionStatus.DISCONNECTED

    # ----------------------------------------
    # NODE DISCOVERY
    # ----------------------------------------
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
    # CONNECT + DISCOVER NODES
    # ----------------------------------------
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
            self.client.connect()

            # Optional metadata read
            try:
                self.server_name = (
                    self.client.get_node("ns=0;i=2254").get_value() or ""
                )
            except Exception:
                self.server_name = ""

            # Build node map fresh on each connection
            root_node = self.client.get_node(self.root_node_id)
            self.nodes = self._get_readable_nodes(root_node)

            logging.info(f"{self.name}: 🔁 Node map built ({len(self.nodes)} nodes).")
            self.status = ConnectionStatus.CONNECTED
            logging.info(f"{self.name}: ✅ CONNECTED")
            return True

        except Exception as e:
            self.status = ConnectionStatus.DISCONNECTED
            logging.error(f"{self.name}: ❌ Connection error: {e}")
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
