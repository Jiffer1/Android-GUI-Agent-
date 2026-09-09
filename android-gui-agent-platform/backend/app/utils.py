"""Small dependency-free helpers shared by the app and the benchmark adapter.

The adapter runs under the android_world virtualenv and must stay importable
without FastAPI/SQLAlchemy, so shared pure utilities live here (feature 908
review i-1: deduplicated from engine.py / aw_adapter.py).
"""
from PIL import Image


def images_are_similar(img1: Image.Image, img2: Image.Image,
                       threshold: float = 0.02) -> bool:
    """True when two screenshots differ by less than threshold (0-1 scale)."""
    size = (100, 100)
    a = list(img1.resize(size).convert("L").getdata())
    b = list(img2.resize(size).convert("L").getdata())
    diff = sum(abs(p - q) for p, q in zip(a, b))
    return diff / (255 * len(a)) < threshold
