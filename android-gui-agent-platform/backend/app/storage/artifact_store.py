from pathlib import Path
from PIL import Image
from app.config.settings import settings


ARTIFACTS_BASE = Path(settings.ARTIFACTS_DIR)


def save_screenshot(task_id: str, step_index: int, image: Image.Image) -> str:
    dir_path = ARTIFACTS_BASE / "tasks" / task_id / "screenshots"
    dir_path.mkdir(parents=True, exist_ok=True)
    path = dir_path / f"step_{step_index}.png"
    image.save(str(path))
    return str(path)
