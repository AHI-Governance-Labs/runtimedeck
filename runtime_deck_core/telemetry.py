"""Low-frequency GPU telemetry on a worker; no optional Python packages."""

import csv
import subprocess
import threading
import time
from datetime import datetime

from .config import CREATE_NO_WINDOW


def read_gpu_sample():
    result = subprocess.run(
        ["nvidia-smi", "--query-gpu=name,memory.total,memory.used,utilization.gpu,temperature.gpu,power.draw",
         "--format=csv,noheader,nounits"], capture_output=True, text=True, encoding="utf-8",
        errors="replace", timeout=5, creationflags=CREATE_NO_WINDOW,
    )
    if result.returncode:
        raise OSError((result.stderr or result.stdout).strip())
    gpus = []
    for fields in csv.reader(result.stdout.splitlines()):
        if len(fields) != 6:
            continue
        gpu = {"name": fields[0].strip()}
        for key, value in zip(("total_mib", "used_mib", "utilization", "temperature", "power_watts"), fields[1:]):
            try:
                gpu[key] = float(value.strip())
            except ValueError:
                gpu[key] = None
        gpus.append(gpu)
    return {"timestamp": datetime.now().isoformat(), "clock": time.monotonic(), "gpus": gpus}


class GpuMonitor:
    def __init__(self, emit, interval=2):
        self.emit = emit
        self.interval = interval
        self.cancel = threading.Event()
        self.thread = None

    def start(self):
        if self.thread and self.thread.is_alive():
            return
        self.cancel.clear()
        self.thread = threading.Thread(target=self._worker, daemon=True)
        self.thread.start()

    def _worker(self):
        while not self.cancel.is_set():
            try:
                sample = read_gpu_sample()
            except (OSError, subprocess.TimeoutExpired) as exc:
                sample = {"clock": time.monotonic(), "error": str(exc), "gpus": []}
            if not self.cancel.is_set():
                self.emit(sample)
            if self.cancel.wait(self.interval):
                break

    def stop(self):
        self.cancel.set()
