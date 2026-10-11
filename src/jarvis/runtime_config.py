"""Validated development runtime settings. Explicit JSON < environment < CLI."""

import json
import os
from pathlib import Path
from typing import Literal

from pydantic import ConfigDict, Field, field_validator

from jarvis.config import Settings
from jarvis.models import OpenAILocalModel


class RuntimeSettings(Settings):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)
    mode: Literal["development"] = "development"
    runtime_host: Literal["127.0.0.1", "::1"] = "127.0.0.1"
    runtime_port: int = Field(default=8765, ge=1, le=65535)
    worker_credentials_file: Path | None = None
    initiative_interval_seconds: float = Field(default=2, ge=0.05, le=3600)
    notification_interval_seconds: float = Field(default=2, ge=0.05, le=3600)
    recovery_interval_seconds: float = Field(default=5, ge=0.05, le=3600)
    maintenance_interval_seconds: float = Field(default=30, ge=0.05, le=3600)
    worker_batch_size: int = Field(default=20, ge=1, le=100)
    max_workers: int = Field(default=1, ge=1, le=1)
    max_worker_principals: int = Field(default=4, ge=1, le=16)
    max_requests: int = Field(default=8, ge=1, le=32)
    startup_timeout_seconds: float = Field(default=30, ge=1, le=300)
    shutdown_timeout_seconds: float = Field(default=15, ge=1, le=300)
    simulated_notification_channels: bool = True
    request_body_limit_bytes: int = Field(default=131072, ge=4096, le=131072)
    request_body_timeout_seconds: float = Field(default=10, ge=0.1, le=60)

    @field_validator("local_model_endpoint")
    @classmethod
    def loopback_model(cls, value):
        # Use the existing SSRF/credential/redirect boundary, not another URL validator.
        return OpenAILocalModel(endpoint=value).endpoint

    @property
    def resolved_runtime_database_path(self):
        return self.database_path or self.data_dir / "runtime" / "journal.sqlite3"

    @classmethod
    def load(cls, config_file: Path | None = None, **overrides):
        values = {}
        if config_file is not None:
            if config_file.stat().st_size > 65536:
                raise ValueError("configuration_too_large")
            values = json.loads(config_file.read_text())
            if not isinstance(values, dict):
                raise ValueError("configuration_object_required")
            if set(values) - cls.model_fields.keys():
                raise ValueError("configuration_unknown_fields")
        for name in cls.model_fields:
            raw = os.environ.get("JARVIS_" + name.upper())
            if raw is not None:
                values[name] = raw
        values.update({k: v for k, v in overrides.items() if v is not None})
        return cls.model_validate(values)
