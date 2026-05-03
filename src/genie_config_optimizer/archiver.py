from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class Archive:
    def __init__(self, root: str | Path = "archive"):
        self.root = Path(root)

    def new_run(self) -> "RunDir":
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")
        path = self.root / ts
        path.mkdir(parents=True, exist_ok=False)
        return RunDir(path)


class RunDir:
    def __init__(self, path: Path):
        self.path = path

    def write_before(self, serialized_space: dict[str, Any]) -> Path:
        return self._write_json("before.json", serialized_space)

    def write_after(self, serialized_space: dict[str, Any]) -> Path:
        return self._write_json("after.json", serialized_space)

    def write_meta(self, meta: dict[str, Any]) -> Path:
        return self._write_json("meta.json", meta)

    def _write_json(self, name: str, payload: Any) -> Path:
        out = self.path / name
        out.write_text(
            json.dumps(payload, indent=2, default=str, ensure_ascii=False),
            encoding="utf-8",
        )
        return out
