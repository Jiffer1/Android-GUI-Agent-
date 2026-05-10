import subprocess
from typing import Dict, List

from app.device.android_adb import AndroidAdbController

_registry: Dict[str, AndroidAdbController] = {}


def get_controller(serial: str) -> AndroidAdbController:
    if serial not in _registry:
        _registry[serial] = AndroidAdbController(serial)
    return _registry[serial]


def list_devices() -> List[str]:
    try:
        result = subprocess.run(["adb", "devices"], capture_output=True, timeout=5)
        lines = result.stdout.decode(errors="replace").splitlines()
        return [
            line.split()[0]
            for line in lines[1:]
            if line.strip() and "device" in line and not line.startswith("*")
        ]
    except FileNotFoundError:
        return []
    except Exception:
        return []
