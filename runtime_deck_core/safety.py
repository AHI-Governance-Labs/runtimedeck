"""Runtime-owned job guards; they never terminate unrelated system processes."""

def thermal_violation(sample, limit):
    if limit <= 0:
        return ""
    for gpu in sample.get("gpus", []):
        temperature = gpu.get("temperature")
        if isinstance(temperature, (int, float)) and temperature >= limit:
            return f"Thermal guard: {gpu['name']} reached {temperature:.0f} °C (limit {limit} °C)."
    return ""


def verify_reported_parameters(settings, metrics):
    """Compare requested flags with runtime-reported configuration, not kernel behavior."""
    mapping = {"threads": "n_threads", "ngl": "n_gpu_layers", "batch": "n_batch", "ubatch": "n_ubatch"}
    rows = metrics.get("benchmark_rows", [])
    observations = []
    mismatches = []
    for row in rows:
        observed = {}
        for requested, reported in mapping.items():
            if reported in row and requested in settings:
                observed[requested] = row[reported]
                if row[reported] != settings[requested]:
                    mismatches.append({"parameter": requested, "requested": settings[requested], "reported": row[reported]})
        if "flash_attn" in row and "flash" in settings:
            observed["flash"] = row["flash_attn"]
            expected = 1 if settings["flash"] else 0
            if row["flash_attn"] != expected:
                mismatches.append({"parameter": "flash", "requested": expected, "reported": row["flash_attn"]})
        observations.append(observed)
    return {"state": "mismatch" if mismatches else "runtime_reported" if observations else "not_reported",
            "observations": observations, "mismatches": mismatches}
