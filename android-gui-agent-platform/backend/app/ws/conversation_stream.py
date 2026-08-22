from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.ws.connection_manager import manager

router = APIRouter()


@router.websocket("/ws/conversations/{conversation_id}")
async def conversation_websocket(websocket: WebSocket, conversation_id: str):
    await manager.connect(conversation_id, websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(conversation_id, websocket)
    except Exception:
        manager.disconnect(conversation_id, websocket)
