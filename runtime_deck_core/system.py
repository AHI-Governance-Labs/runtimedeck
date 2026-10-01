"""Platform integration and GPU probing outside the GUI thread."""

import os
import subprocess
import sys
from pathlib import Path

from .config import CREATE_NO_WINDOW


def open_folder(path):
    path = Path(path)
    path.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        os.startfile(path)
    else:
        subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", str(path)])


def probe_nvidia_gpu():
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,driver_version,memory.total,memory.used,temperature.gpu",
             "--format=csv,noheader"], capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=8, creationflags=CREATE_NO_WINDOW,
        )
        if result.returncode:
            return f"NVIDIA unavailable: {(result.stderr or result.stdout).strip()}"
        return result.stdout.strip() or "No NVIDIA GPU detected."
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"NVIDIA unavailable: {exc}"
