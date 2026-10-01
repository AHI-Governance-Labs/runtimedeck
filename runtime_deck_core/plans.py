"""Persisted experiment objectives and evidence-based acceptance criteria."""

import json
import math
import os
import tempfile
from pathlib import Path


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(value, stream, indent=2, ensure_ascii=False, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def validate_criteria(data):
    limits = {"generation_tps": (0, 100000), "quality_pass_pct": (0, 100),
              "temperature_c": (0, 110), "memory_mib": (0, 1000000), "stability_runs": (1, 100)}
    result = {}
    for key, bounds in limits.items():
        raw = data[key]
        if isinstance(raw, bool):
            raise ValueError(f"{key}: se requiere un número")
        value = float(raw)
        if not math.isfinite(value) or not bounds[0] <= value <= bounds[1]:
            raise ValueError(f"{key}: el valor debe estar entre {bounds[0]} y {bounds[1]}")
        if key == "stability_runs" and not value.is_integer():
            raise ValueError("Las repeticiones de estabilidad deben ser enteras")
        result[key] = int(value) if key == "stability_runs" else value
    return result


def assess_plan(document):
    """Missing or partial measurements remain pending, never silently pass."""
    criteria = document["criteria"]
    stages = document.get("results", {})
    stable = stages.get("stability", [])
    answers = stages.get("answers", [])
    rows = []

    def row(name, target, measured, passed):
        rows.append({"criterion": name, "target": target, "measured": measured,
                     "state": "pendiente" if passed is None else "cumple" if passed else "no cumple"})

    successful = [record for record in stable if record.get("status") == "success" and record.get("exit_code") == 0
                  and not record.get("parameter_verification", {}).get("mismatches")]
    required = criteria["stability_runs"]
    row("Estabilidad", f"{required} ejecuciones correctas", len(successful),
        len(successful) >= required if stable else None)
    speeds = [record.get("metrics", {}).get("generation_tps") for record in successful]
    speeds = [value for value in speeds if isinstance(value, (int, float)) and math.isfinite(value)]
    measured = min(speeds) if len(speeds) >= required else None
    row("Velocidad sostenida", f"≥ {criteria['generation_tps']:g} t/s (mínimo de la suite)", measured,
        measured >= criteria["generation_tps"] if measured is not None else None)
    quality = answers[-1] if answers else {}
    metrics = quality.get("metrics", {})
    completed = (quality.get("status") == "success" and metrics.get("cases_total", 0) > 0
                 and metrics.get("cases_completed") == metrics.get("cases_total"))
    score = metrics.get("quality_pass_pct") if completed else None
    row("Calidad del dataset", f"≥ {criteria['quality_pass_pct']:g}%", score,
        score >= criteria["quality_pass_pct"] if isinstance(score, (int, float)) and math.isfinite(score) else None)
    samples = [gpu for sample in document.get("telemetry", []) for gpu in sample.get("gpus", [])]
    for name, criterion, source in (("Temperatura muestreada", "temperature_c", "temperature"),
                                    ("VRAM total muestreada", "memory_mib", "used_mib")):
        limit = criteria[criterion]
        if limit == 0:
            continue
        values = [gpu[source] for gpu in samples if isinstance(gpu.get(source), (int, float)) and math.isfinite(gpu[source])]
        peak = max(values) if values else None
        row(name, f"≤ {limit:g}", peak, peak <= limit if peak is not None else None)
    state = "no cumple" if any(item["state"] == "no cumple" for item in rows) else "pendiente" if any(item["state"] == "pendiente" for item in rows) else "cumple"
    if document.get("status") != "finished":
        state = "incompleto"
    return {"state": state, "criteria": rows,
            "scope": "Conclusión limitada al modelo, configuración, dataset y muestras registrados. No demuestra protección térmica continua ni calidad general."}
