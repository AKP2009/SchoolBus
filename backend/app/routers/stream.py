import json

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.ws import manager

router = APIRouter(tags=["stream"])


@router.websocket("/stream/{machine_id}")
async def stream(websocket: WebSocket, machine_id: str) -> None:
    """Live messages for one machine (telemetry, alert, safety, health, fatigue).

    The client sends {"kind": "ping"} every 20 s; any message counts as a sign of life. Other
    client messages are ignored.
    """
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
