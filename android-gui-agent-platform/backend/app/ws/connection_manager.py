from typing import Dict, List

from fastapi import WebSocket


class ConnectionManager:
    def __init__(self):
        self._connections: Dict[str, List[WebSocket]] = {}

    async def connect(self, task_id: str, websocket: WebSocket):
        await websocket.accept()
        self._connections.setdefault(task_id, []).append(websocket)

    def disconnect(self, task_id: str, websocket: WebSocket):
        conns = self._connections.get(task_id, [])
        if websocket in conns:
            conns.remove(websocket)

    async def broadcast(self, task_id: str, message: dict):
        dead = []
        for ws in list(self._connections.get(task_id, [])):
            try:
                await ws.send_json(message)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(task_id, ws)


manager = ConnectionManager()
