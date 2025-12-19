from typing import Any

import logging
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from opcua import ua

from auth import current_user
from db_async import get_async_session
from models_user import User as DBUser
from parks import PARKS, user_allowed_urls
from app.broadcast import get_plc_clients
from app.opcua_client import ConnectionStatus
from app.schemas import WriteRequest

router = APIRouter(tags=["write"])


@router.post("/write_value")
async def write_plc_value(
    req: WriteRequest,
    user: DBUser = Depends(current_user),
    session: AsyncSession = Depends(get_async_session),
):
    """
    Generic write endpoint.

    - Scalar values (numbers / bools / strings) are written directly to the node
      with name == req.node_name (this is what the setpoint inputs use).
    - For CMD_Instant_Cutoff we support a special array write:
        value = [True, False]  -> bit0=False, bit1=True  (park OFF or ON depending on your mapping)
      and we resolve the correct child nodes [0], [1] under the array node.
    """

    # ------------------------------------------------------------------
    # 1) PERMISSIONS: superuser OR user with access to this park
    # ------------------------------------------------------------------
    if user.is_superuser:
        allowed_urls = {info["url"] for info in PARKS.values()}
    else:
        allowed_urls = await user_allowed_urls(session, user)

    if req.plc_url not in allowed_urls:
        raise HTTPException(403, "You do not have write access to this park.")

    # ------------------------------------------------------------------
    # 2) Resolve target PLC client
    # ------------------------------------------------------------------
    clients = get_plc_clients()
    target = next((p for p in clients if p.url == req.plc_url), None)
    if not target or target.status != ConnectionStatus.CONNECTED:
        raise HTTPException(404, "PLC not connected.")
# ------------------------------------------------------------------
# 3) SPECIAL CASE: CMD_Instant_Cutoff as 2-bit boolean array
# ------------------------------------------------------------------


        # ------------------------------------------------------------------
    # 3) SPECIAL CASE: CMD_Instant_Cutoff as 2-bit boolean array
    # ------------------------------------------------------------------
    if isinstance(req.value, list) and req.node_name == "CMD_Instant_Cutoff":
        parent = target.nodes.get("CMD_Instant_Cutoff")
        if not parent:
            logging.error("Array parent node 'CMD_Instant_Cutoff' not found.")
            raise HTTPException(404, "Array node 'CMD_Instant_Cutoff' not found.")

        # 🔒 LOCK START
        with target.lock:
            try:
                children = parent.get_children()
            except Exception as e:
                logging.error(f"Failed to get children for CMD_Instant_Cutoff: {e}")
                raise HTTPException(500, "Failed to resolve cutoff child nodes.")

            index_map: dict[int, Any] = {}
            for child in children:
                try:
                    bn = child.get_browse_name().Name
                    if bn.startswith("[") and bn.endswith("]"):
                        idx = int(bn[1:-1])
                        index_map[idx] = child
                except Exception:
                    continue

            if not index_map:
                logging.error("No [index] children found under CMD_Instant_Cutoff.")
                raise HTTPException(404, "Cutoff child bits not found.")

            for idx, bit in enumerate(req.value):
                child = index_map.get(idx)
                if child is None:
                    logging.error(f"Child index [{idx}] not found under CMD_Instant_Cutoff.")
                    raise HTTPException(404, f"Cutoff bit [{idx}] not found.")

                try:
                    dv = ua.DataValue(ua.Variant(bool(bit), ua.VariantType.Boolean))
                    child.set_attribute(ua.AttributeIds.Value, dv)
                    logging.info(
                        f"Write {bit} to 'CMD_Instant_Cutoff[{idx}]' on {target.name}"
                    )
                except Exception as e:
                    logging.error(f"Write failed on CMD_Instant_Cutoff[{idx}]: {e}")
                    raise HTTPException(500, f"Write failed on cutoff bit [{idx}]: {e}")

        # 🔓 LOCK END
        return {"status": "success", "written": req.value}
    
    # For any other list value we don't support array writes yet
    if isinstance(req.value, list):
        raise HTTPException(
            400,
            "Array writes are only supported for 'CMD_Instant_Cutoff' at the moment.",
    )


    # ------------------------------------------------------------------
    # 4) NORMAL SCALAR WRITE
    # ------------------------------------------------------------------
    node = target.nodes.get(req.node_name)
    if not node:
        raise HTTPException(404, f"Node '{req.node_name}' not found.")

    # 🔒 LOCK START
    with target.lock:
        try:
            vt = node.get_data_type_as_variant_type()
            v: Any = req.value

            if vt == ua.VariantType.Boolean:
                v = bool(v)
            elif vt in (
                ua.VariantType.Int16,
                ua.VariantType.Int32,
                ua.VariantType.Int64,
                ua.VariantType.UInt16,
                ua.VariantType.UInt32,
                ua.VariantType.UInt64,
            ):
                v = int(v)
            elif vt in (ua.VariantType.Float, ua.VariantType.Double):
                v = float(v)

            try:
                before = node.get_value()
                logging.info(
                    f"[WRITE DEBUG] Before write '{req.node_name}' on {target.name}: "
                    f"{before!r} (type={type(before).__name__}, vt={vt})"
                )
            except Exception:
                pass

            dv = ua.DataValue(ua.Variant(v, vt))
            node.set_attribute(ua.AttributeIds.Value, dv)
            logging.info(
                f"USER={user.email} WRITE {v} -> {req.node_name} "
                f"ON {target.name} ({req.plc_url})"
            )

            try:
                after = node.get_value()
                logging.info(
                    f"[WRITE DEBUG] After write '{req.node_name}' on {target.name}: "
                    f"{after!r} (type={type(after).__name__})"
                )
            except Exception:
                pass

            return {"status": "success"}

        except Exception as e:
            logging.error(f"Write failed: {e}")
            raise HTTPException(500, "Write failed.")
    # 🔓 LOCK END
