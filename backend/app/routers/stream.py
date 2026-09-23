from fastapi import APIRouter, WebSocket, WebSocketDisconnect

router = APIRouter(tags=["stream"])


@router.websocket("/stream/{machine_id}")
async def stream(websocket: WebSocket, machine_id: str) -> None:
    # Skeleton: accepts the connection and ignores pings. The replay engine will push
    # schemas.stream.ServerMessage objects here.
    await websocket.accept()
    try:
        while True:
            await websocket.receive_json()
    except WebSocketDisconnect:
        pass
