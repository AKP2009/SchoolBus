import json

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.core.auth import websocket_user
from app.core.errors import ApiError
from app.ws import manager

router = APIRouter(tags=["stream"])


@router.websocket("/stream/{machine_id}")
async def stream(websocket: WebSocket, machine_id: str) -> None:
    """Live messages for one machine (telemetry, alert, safety, health, fatigue).

    Auth: `?token=<Supabase access token>` (browsers can't set headers on a WebSocket) or a
    bearer header. Without a valid token the socket closes with 4401 (4403 if not allowed).
    The client sends {"kind": "ping"} every 20 s; any message counts as a sign of life. Other
    client messages are ignored.
    """
    try:
        await websocket_user(websocket)
    except ApiError as e:
        # accept first: a close before the handshake reaches the browser as a bare HTTP 403
        await websocket.accept()
        await websocket.close(code=4403 if e.status_code == 403 else 4401, reason=e.message)
        return
    await manager.connect(machine_id, websocket)
    try:
        while True:
            text = await websocket.receive_text()
            manager.touch(websocket)
            try:
                msg = json.loads(text)
            except ValueError:
                continue
            if not isinstance(msg, dict) or msg.get("kind") != "ping":
                continue
    except WebSocketDisconnect:
        pass
    finally:
        await manager.disconnect(machine_id, websocket)
