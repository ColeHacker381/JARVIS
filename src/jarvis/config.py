"""Small process-environment configuration without secrets or cloud adapters."""

import os
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, field_validator


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    data_dir: Path = Path(".jarvis")
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    cloud_ai_enabled: Literal[False] = False

    @field_validator("data_dir", mode="before")
    @classmethod
    def nonempty_path(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            raise ValueError("data directory must not be empty")
        return value

    @classmethod
    def from_environment(cls) -> "Settings":
        return cls.model_validate(
            {
                "data_dir": os.environ.get("JARVIS_DATA_DIR", ".jarvis"),
                "log_level": os.environ.get("JARVIS_LOG_LEVEL", "INFO"),
            }
        )
