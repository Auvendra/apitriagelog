"""Runtime settings. Every value can be overridden with an environment variable."""
import os
from dataclasses import dataclass
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


@dataclass
class Settings:
    api_key: str = os.getenv("TRIAGE_API_KEY", "test_secret_123")
    db_path: Path = Path(os.getenv("TRIAGE_DB_PATH", str(BASE_DIR / "tickets.db")))
    app_version: str = "1.0.0"


settings = Settings()
