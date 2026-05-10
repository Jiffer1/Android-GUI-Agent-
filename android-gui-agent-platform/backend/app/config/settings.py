from pydantic_settings import BaseSettings
from pathlib import Path
from typing import List


class Settings(BaseSettings):
    DATABASE_URL: str = "sqlite:///./data/app.db"
    ARTIFACTS_DIR: str = str(Path(__file__).parent.parent.parent.parent.parent / "artifacts")
    CORS_ORIGINS: List[str] = ["http://localhost:5173", "http://127.0.0.1:5173"]
    MAX_STEPS: int = 20

    VLM_API_KEY: str = ""
    VLM_API_URL: str = "https://ark.cn-beijing.volces.com/api/v3"
    VLM_MODEL_ID: str = "doubao-seed-1-6-vision-250815"

    class Config:
        env_file = ".env"


settings = Settings()
