"""Group throughput records by their recorded model and token workload."""

import math


def workload_key(record):
    model = record.get("model") or {}
    parameters = record.get("parameters") or {}
    identity = model.get("path")
    tokens = tuple(parameters.get(key) for key in ("prompt_tokens", "generation_tokens", "repetitions"))
    if not identity or any(isinstance(value, bool) or not isinstance(value, int) or value <= 0 for value in tokens):
        return None
    return (identity, model.get("size"), *tokens)


def comparable_throughput(records, reference=None):
    valid = [record for record in records if record.get("status") == "success"
             and record.get("exit_code") == 0
             and not (record.get("parameter_verification") or {}).get("mismatches")
             and isinstance((record.get("metrics") or {}).get("generation_tps"), (int, float))
             and math.isfinite(record["metrics"]["generation_tps"])
             and record["metrics"]["generation_tps"] > 0 and workload_key(record) is not None]
    if reference is None:
        reference = valid[0] if valid else {}
    key = workload_key(reference)
    return [record for record in valid if key is not None and workload_key(record) == key]
