"""Benchmark metadata belongs to the run, independent of current selection."""

import json
import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from .metrics import explain_failure, parse_metrics
from .safety import verify_reported_parameters
from .plans import atomic_json


@dataclass
class BenchmarkSession:
    root: Path
    model: dict
    runtime: dict
    command: list[str]
    parameters: dict
    started: datetime = field(default_factory=datetime.now)
    output: list[str] = field(default_factory=list)
    settings: dict = field(default_factory=dict)
    experiment: str = "benchmark"
    label: str = ""
    status: str = ""
    clock_started: float = field(default_factory=time.monotonic)
    telemetry: list[dict] = field(default_factory=list)
    dataset: dict = field(default_factory=dict)
    extra_metrics: dict = field(default_factory=dict)
    plan_id: str = ""

    def save(self, exit_code):
        directory = self.root / "benchmarks"
        directory.mkdir(parents=True, exist_ok=True)
        model_name = re.sub(r"[^A-Za-z0-9_.-]+", "_", self.model["name"])
        stem = f"{self.started:%Y%m%d-%H%M%S-%f}-{model_name}"
        raw_path = directory / f"{stem}.txt"
        raw_path.write_text("".join(self.output), encoding="utf-8", errors="replace")
        metrics = parse_metrics("".join(self.output))
        metrics.update(self.extra_metrics)
        gpu_samples = [gpu for sample in self.telemetry for gpu in sample.get("gpus", [])]
        for key, metric in (("used_mib", "gpu_peak_memory_mib"), ("temperature", "gpu_peak_temperature_c")):
            values = [gpu[key] for gpu in gpu_samples if isinstance(gpu.get(key), (float, int))]
            if values:
                metrics[metric] = max(values)
        metadata = {
            "schema_version": 2, "experiment": self.experiment, "label": self.label,
            "plan_id": self.plan_id,
            "timestamp": self.started.isoformat(), "finished": datetime.now().isoformat(),
            "model": self.model, "runtime": self.runtime, "command": self.command,
            "exit_code": exit_code, "parameters": self.parameters, "raw_output_file": str(raw_path),
            "settings": self.settings, "metrics": metrics, "telemetry": self.telemetry,
            "dataset": self.dataset,
            "parameter_verification": verify_reported_parameters(self.settings, metrics),
            "duration_seconds": round(time.monotonic() - self.clock_started, 3),
            "status": self.status or ("success" if exit_code == 0 else "failed"),
            "diagnosis": explain_failure("".join(self.output), exit_code),
        }
        atomic_json(directory / f"{stem}.json", metadata)
        return raw_path
