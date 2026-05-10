from fastapi import APIRouter

from app.device.registry import list_devices

router = APIRouter()


@router.get("/api/devices")
def get_devices():
    serials = list_devices()
    return {
        "devices": [{"serial": s, "status": "online"} for s in serials],
        "count": len(serials),
    }
