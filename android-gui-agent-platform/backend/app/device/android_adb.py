import io
import re
import subprocess
from typing import List, Optional, Tuple

from PIL import Image

from app.device.base import BaseDeviceController, AdbNotFoundError, DeviceError


class AndroidAdbController(BaseDeviceController):
    def __init__(self, serial: str):
        self.serial = serial
        self._screen_size: Optional[Tuple[int, int]] = None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _run(self, cmd: List[str], timeout: int = 10) -> subprocess.CompletedProcess:
        try:
            result = subprocess.run(cmd, capture_output=True, timeout=timeout)
            if result.returncode != 0:
                raise DeviceError(result.stderr.decode(errors="replace").strip())
            return result
        except FileNotFoundError:
            raise AdbNotFoundError("adb not found. Install Android SDK Platform Tools and add adb to PATH.")
        except subprocess.TimeoutExpired:
            raise DeviceError(f"ADB command timed out after {timeout}s")

    def _to_pixels(self, point: List[int]) -> Tuple[int, int]:
        w, h = self.get_screen_size()
        x = max(0, min(int(point[0] / 1000 * w), w - 1))
        y = max(0, min(int(point[1] / 1000 * h), h - 1))
        return x, y

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def list_devices(self) -> List[str]:
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

    def screenshot(self) -> Image.Image:
        result = self._run(
            ["adb", "-s", self.serial, "exec-out", "screencap", "-p"],
            timeout=15,
        )
        return Image.open(io.BytesIO(result.stdout))

    def click(self, point: List[int]) -> None:
        x, y = self._to_pixels(point)
        self._run(["adb", "-s", self.serial, "shell", "input", "tap", str(x), str(y)])

    def scroll(self, start_point: List[int], end_point: List[int]) -> None:
        x1, y1 = self._to_pixels(start_point)
        x2, y2 = self._to_pixels(end_point)
        self._run([
            "adb", "-s", self.serial, "shell", "input", "swipe",
            str(x1), str(y1), str(x2), str(y2), "300",
        ])

    def type_text(self, text: str) -> None:
        escaped = text.replace("\\", "\\\\").replace(" ", "%s").replace("'", "\\'")
        self._run(["adb", "-s", self.serial, "shell", "input", "text", escaped])

    def open_app(self, package_or_name: str) -> None:
        self._run([
            "adb", "-s", self.serial, "shell",
            "monkey", "-p", package_or_name, "-c", "android.intent.category.LAUNCHER", "1",
        ])

    def back(self) -> None:
        self._run(["adb", "-s", self.serial, "shell", "input", "keyevent", "KEYCODE_BACK"])

    def home(self) -> None:
        self._run(["adb", "-s", self.serial, "shell", "input", "keyevent", "KEYCODE_HOME"])

    def get_screen_size(self) -> Tuple[int, int]:
        if self._screen_size:
            return self._screen_size
        result = self._run(["adb", "-s", self.serial, "shell", "wm", "size"])
        output = result.stdout.decode(errors="replace")
        match = re.search(r"(\d+)x(\d+)", output)
        if not match:
            raise DeviceError(f"Cannot parse screen size from: {output!r}")
        self._screen_size = (int(match.group(1)), int(match.group(2)))
        return self._screen_size
