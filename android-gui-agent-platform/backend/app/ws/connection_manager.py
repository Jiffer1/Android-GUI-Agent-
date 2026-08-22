import asyncio
from typing import Dict, List

from fastapi import WebSocket


class ConnectionManager:
    """Tracks WebSocket connections per conversation.

    Turns run on a dedicated background loop, so broadcasts may arrive from
    a thread other than the one owning the sockets; sends are marshalled
    back to each socket's own loop.
    """

    def __init__(self):
        self._connections: Dict[str, List[WebSocket]] = {}
        self._loops: Dict[int, asyncio.AbstractEventLoop] = {}

    async def connect(self, conversation_id: str, websocket: WebSocket):
        await websocket.accept()
        self._connections.setdefault(conversation_id, []).append(websocket)
        try:
            self._loops[id(websocket)] = asyncio.get_running_loop()
        except RuntimeError:
            pass

    def disconnect(self, conversation_id: str, websocket: WebSocket):
        conns = self._connections.get(conversation_id, [])
        if websocket in conns:
            conns.remove(websocket)
        self._loops.pop(id(websocket), None)

    async def broadcast(self, conversation_id: str, message: dict):
        dead = []
        try:
            current = asyncio.get_running_loop()
        except RuntimeError:
            current = None
        for ws in list(self._connections.get(conversation_id, [])):
            try:
                loop = self._loops.get(id(ws))
                if loop is None or loop is current:
                    await ws.send_json(message)
                else:
                    await asyncio.wait_for(
                        asyncio.wrap_future(
                            asyncio.run_coroutine_threadsafe(ws.send_json(message), loop)),
                        timeout=5.0)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(conversation_id, ws)


manager = ConnectionManager()
