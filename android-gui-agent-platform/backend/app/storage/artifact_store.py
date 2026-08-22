from pathlib import Path
from PIL import Image
from app.config.settings import settings


ARTIFACTS_BASE = Path(settings.ARTIFACTS_DIR)


def save_screenshot(conversation_id: str, turn_id: str, step_index: int, image: Image.Image) -> str:
    """Persist a step screenshot and return its path relative to ARTIFACTS_DIR.

    The relative posix path (e.g. ``conversations/<cid>/turns/<tid>/step_0.png``)
    is what gets stored on TurnStep so the API can build ``/artifacts/...``
    URLs without dealing with absolute Windows paths.
    """
    rel_dir = Path("conversations") / conversation_id / "turns" / turn_id / "screenshots"
    dir_path = ARTIFACTS_BASE / rel_dir
    dir_path.mkdir(parents=True, exist_ok=True)
    path = dir_path / f"step_{step_index}.png"
    image.save(str(path))
    return (rel_dir / f"step_{step_index}.png").as_posix()
