"""Validated preferences and atomic JSON persistence."""

import json
import math
import os
import tempfile
from pathlib import Path

from .config import DEFAULT_ROOT, SETTINGS_PATH
from .discovery import normalize_workspace

DEFAULTS = {
    "root": str(DEFAULT_ROOT), "ctx": 8192, "ngl": 99,
    "model_path": "", "runtime_directory": "",
    "threads": max(1, (os.cpu_count() or 8) // 2),
    "batch": 512, "ubatch": 256, "max_tokens": 256,
    "temp": 0.7, "top_p": 0.95, "top_k": 40, "seed": -1,
    "flash": True, "mmap": True,
    "prompt": "Explica brevemente qué runtime estás usando.",
    "server_port": 8080, "server_host": "127.0.0.1",
    "server_parallel": 1,
    "kv_cache_enabled": False, "kv_ott_core": "",
    "kv_cache_directory": str(SETTINGS_PATH.parent / "kv-sessions"),
    "kv_cache_budget_mib": 4096, "kv_cache_max_mib": 512,
    "bench_prompt": 512, "bench_gen": 128, "bench_reps": 3,
    "thermal_limit": 85, "job_timeout": 600,
}
LIMITS = {
    "ctx": (256, 1048576), "ngl": (0, 999), "threads": (1, 256),
    "batch": (1, 8192), "ubatch": (1, 8192), "max_tokens": (1, 100000),
    "temp": (0, 5), "top_p": (0, 1), "top_k": (0, 10000),
    "seed": (-1, 2147483647), "server_port": (1, 65535),
    "server_parallel": (1, 64),
    "kv_cache_budget_mib": (64, 1048576), "kv_cache_max_mib": (1, 4096),
    "bench_prompt": (1, 100000), "bench_gen": (1, 100000), "bench_reps": (1, 100),
    "thermal_limit": (0, 110), "job_timeout": (5, 86400),
}


def validate_value(key, value):
    default = DEFAULTS[key]
    if isinstance(default, bool):
        if not isinstance(value, bool):
            raise ValueError(f"{key}: expected a boolean.")
    elif isinstance(default, (int, float)):
        if isinstance(value, bool):
            raise ValueError(f"{key}: expected a number.")
        try:
            number = float(value)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError(f"{key}: expected a number.") from exc
        if not math.isfinite(number) or not LIMITS[key][0] <= number <= LIMITS[key][1]:
            raise ValueError(f"{key}: enter a value between {LIMITS[key][0]} and {LIMITS[key][1]}.")
        if isinstance(default, int) and not number.is_integer():
            raise ValueError(f"{key}: expected a whole number.")
        value = int(number) if isinstance(default, int) else number
    elif not isinstance(value, str):
        raise ValueError(f"{key}: expected text.")
    if key in {"kv_ott_core", "kv_cache_directory"}:
        value = value.strip()
    if key in {"root", "server_host", "kv_cache_directory"} and not value.strip():
        raise ValueError(f"{key}: cannot be empty.")
    return value


def validate_settings(data):
    return {key: validate_value(key, data.get(key, default)) for key, default in DEFAULTS.items()}


class SettingsStore:
    def __init__(self, path: Path = SETTINGS_PATH):
        self.path = Path(path)

    def load(self):
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return DEFAULTS.copy()
        if not isinstance(data, dict):
            return DEFAULTS.copy()
        result = DEFAULTS.copy()
        for key in DEFAULTS:
            if key in data:
                try:
                    result[key] = validate_value(key, data[key])
                except ValueError:
                    pass
        root = Path(result["root"]).expanduser()
        result["root"] = str(normalize_workspace(root if root.is_dir() else DEFAULT_ROOT))
        return result

    def save(self, data):
        values = validate_settings(data)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=self.path.parent,
                                             prefix="settings-", suffix=".tmp", delete=False) as stream:
                temporary = Path(stream.name)
                json.dump(values, stream, indent=2, ensure_ascii=False)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
