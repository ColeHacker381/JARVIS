"""Adapter-independent persistence failures; messages never contain stored payloads."""


class StorageError(RuntimeError):
    def __init__(self, code: str = "storage_unavailable") -> None:
        self.code = code
        super().__init__(code)
