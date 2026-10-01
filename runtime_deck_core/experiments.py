"""Bounded experimental designs and comparable, measured recommendations."""

import itertools
from dataclasses import dataclass

from .settings import validate_settings, validate_value


@dataclass
class Trial:
    label: str
    values: dict
    kind: str = "benchmark"
    dataset: str = ""


def numeric_axis(text, key):
    pieces = [piece.strip() for piece in text.split(",") if piece.strip()]
    if not pieces:
        raise ValueError(f"{key}: enter at least one value.")
    return list(dict.fromkeys(validate_value(key, piece) for piece in pieces))


def tuning_plan(base, layers, threads, batches, limit=16):
    base = validate_settings(base)
    axes = (numeric_axis(layers, "ngl"), numeric_axis(threads, "threads"), numeric_axis(batches, "batch"))
    count = len(axes[0]) * len(axes[1]) * len(axes[2])
    if not 1 <= int(limit) <= 256 or count > int(limit):
        raise ValueError(f"The grid has {count} trials; limit is {limit}. Reduce the axes or increase the limit (max 256).")
    result = []
    for ngl, n_threads, batch in itertools.product(*axes):
        values = dict(base, ngl=ngl, threads=n_threads, batch=batch, ubatch=min(base["ubatch"], batch))
        result.append(Trial(f"GPU {ngl} · threads {n_threads} · batch {batch}", values))
    return result


def scaling_plan(base, prompts):
    return [Trial(f"Prompt {tokens} tokens", dict(base, bench_prompt=tokens))
            for tokens in numeric_axis(prompts, "bench_prompt")]


def stability_plan(base, repetitions):
    if not 1 <= int(repetitions) <= 100:
        raise ValueError("Stability runs: enter a value between 1 and 100.")
    return [Trial(f"Stability {index + 1}/{repetitions}", dict(base)) for index in range(int(repetitions))]


def best_result(records, objective="generation_tps"):
    valid = [record for record in records if record.get("exit_code") == 0
             and record.get("status") == "success"
             and not record.get("parameter_verification", {}).get("mismatches")
             and isinstance(record.get("metrics", {}).get(objective), (float, int))]
    return max(valid, key=lambda record: record["metrics"][objective]) if valid else None
