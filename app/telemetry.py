from typing import Any, Dict, List
import math


def safe_value(v: Any) -> Any:
    if isinstance(v, float):
        if math.isnan(v) or math.isinf(v):
            return None
    return v


def dict_client_to_view(d: Dict[str, Any]) -> Dict[str, Any]:
    nodes = d.get("nodes") or {}
    nodes_list = [{"name": k, "value": safe_value(v)} for k, v in nodes.items()]

    return {
        "name": d.get("name") or "",
        "url": d.get("url") or "",
        "status": d.get("status") or "DISCONNECTED",
        "nodes": nodes_list,
    }


def payload_from_raw_list(raw_list: List[Dict[str, Any]]) -> Dict[str, Any]:
    return {"plc_clients": [dict_client_to_view(d) for d in raw_list]}
