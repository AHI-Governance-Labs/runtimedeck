"""Inspect, compare and export experiment artifacts, including older records."""

import csv
import json
from pathlib import Path

from .metrics import parse_metrics


def load_history(root):
    results, warnings = [], []
    for path in sorted((Path(root) / "benchmarks").glob("*.json"), reverse=True):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(record, dict) or "exit_code" not in record:
                continue
            for field in ("model", "runtime", "parameters", "metrics", "settings"):
                if field in record and record[field] is not None and not isinstance(record[field], dict):
                    raise ValueError(f"Invalid {field} metadata")
                if field in record and record[field] is None:
                    record[field] = {}
            record["artifact"] = str(path)
            if "metrics" not in record:
                raw_path = Path(record.get("raw_output_file", path.with_suffix(".txt")))
                record["metrics"] = parse_metrics(raw_path.read_text(encoding="utf-8", errors="replace"))
            record.setdefault("status", "success" if record["exit_code"] == 0 else "failed")
            results.append(record)
        except (OSError, ValueError, TypeError) as exc:
            warnings.append(f"{path.name}: {exc}")
    return results, warnings


def export_csv(records, path):
    with Path(path).open("w", encoding="utf-8-sig", newline="") as stream:
        fields = ["timestamp", "model", "runtime", "status", "exit_code", "prompt_tps", "generation_tps",
                  "perplexity", "quality_pass_pct", "cases_total", "cases_completed", "cases_passed",
                  "duration_seconds", "gpu_layers", "threads", "artifact"]
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for record in records:
            writer.writerow({
                "timestamp": record.get("timestamp", ""), "model": (record.get("model") or {}).get("name", ""),
                "runtime": (record.get("runtime") or {}).get("label", ""), "status": record.get("status", ""),
                "exit_code": record.get("exit_code"), "duration_seconds": record.get("duration_seconds", ""),
                "gpu_layers": record.get("parameters", {}).get("gpu_layers", ""),
                "threads": record.get("parameters", {}).get("threads", ""), "artifact": record.get("artifact", ""),
                **{key: (record.get("metrics") or {}).get(key, "") for key in
                   ("prompt_tps", "generation_tps", "perplexity", "quality_pass_pct", "cases_total", "cases_completed", "cases_passed")},
            })
