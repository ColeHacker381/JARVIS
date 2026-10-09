"""Small process-environment configuration without secrets or cloud adapters."""

import os
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, field_validator


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    data_dir: Path = Path(".jarvis")
    database_path: Path | None = None
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    cloud_ai_enabled: Literal[False] = False

    @field_validator("data_dir", "database_path", mode="before")
    @classmethod
    def nonempty_path(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            raise ValueError("data directory must not be empty")
        return value

    @property
    def resolved_database_path(self) -> Path:
        return self.database_path or self.data_dir / "journal.sqlite3"

    @classmethod
    def from_environment(cls) -> "Settings":
        return cls.model_validate(
            {
                "data_dir": os.environ.get("JARVIS_DATA_DIR", ".jarvis"),
                "log_level": os.environ.get("JARVIS_LOG_LEVEL", "INFO"),
                "database_path": os.environ.get("JARVIS_DATABASE_PATH"),
            }
        )
