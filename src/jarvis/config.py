"""Small process-environment configuration without secrets or cloud adapters."""

import os
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    data_dir: Path = Path(".jarvis")
    database_path: Path | None = None
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    cloud_ai_enabled: Literal[False] = False
    model_provider: Literal["local", "fake"] = "local"
    local_model_endpoint: str = "http://127.0.0.1:11434/v1"
    local_model_name: str = "qwen2.5:3b"
    local_model_response_format: Literal["json_object", "json_schema"] = "json_object"
    model_timeout_seconds: float = Field(default=60.0, gt=0, le=300)

    @field_validator("data_dir", "database_path", mode="before")
    @classmethod
    def nonempty_path(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            raise ValueError("data directory must not be empty")
        return value

    @property
    def resolved_database_path(self) -> Path:
        return self.database_path or self.data_dir / "journal.sqlite3"

    @property
    def resolved_chat_database_path(self) -> Path:
        # Separate explicit development fixture store from ordinary kernel data by default.
        return self.data_dir / "chat" / "journal.sqlite3"

    @classmethod
    def from_environment(cls) -> "Settings":
        return cls.model_validate(
            {
                "data_dir": os.environ.get("JARVIS_DATA_DIR", ".jarvis"),
                "log_level": os.environ.get("JARVIS_LOG_LEVEL", "INFO"),
                "database_path": os.environ.get("JARVIS_DATABASE_PATH"),
                "model_provider": os.environ.get("JARVIS_MODEL_PROVIDER", "local"),
                "local_model_endpoint": os.environ.get(
                    "JARVIS_LOCAL_MODEL_ENDPOINT", "http://127.0.0.1:11434/v1"
                ),
                "local_model_name": os.environ.get("JARVIS_LOCAL_MODEL_NAME", "qwen2.5:3b"),
                "local_model_response_format": os.environ.get(
                    "JARVIS_LOCAL_MODEL_RESPONSE_FORMAT", "json_object"
                ),
                "model_timeout_seconds": float(
                    os.environ.get("JARVIS_MODEL_TIMEOUT_SECONDS", "60")
                ),
            }
        )
