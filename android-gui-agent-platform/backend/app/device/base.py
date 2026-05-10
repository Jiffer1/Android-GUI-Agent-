from abc import ABC, abstractmethod
from typing import List, Tuple
from PIL.Image import Image as PILImage


class BaseDeviceController(ABC):
    @abstractmethod
    def list_devices(self) -> List[str]: ...

    @abstractmethod
    def screenshot(self) -> PILImage: ...

    @abstractmethod
    def click(self, point: List[int]) -> None: ...

    @abstractmethod
    def scroll(self, start_point: List[int], end_point: List[int]) -> None: ...

    @abstractmethod
    def type_text(self, text: str) -> None: ...

    @abstractmethod
    def open_app(self, package_or_name: str) -> None: ...

    @abstractmethod
    def back(self) -> None: ...

    @abstractmethod
    def home(self) -> None: ...

    @abstractmethod
    def get_screen_size(self) -> Tuple[int, int]: ...


class AdbNotFoundError(RuntimeError):
    pass


class DeviceError(RuntimeError):
    pass
