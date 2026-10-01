"""Extract measurements from real runtime output, including mixed stderr/JSON."""

import json
import math
import re


def _number(value):
    try:
        number = float(value)
        return number if math.isfinite(number) and number >= 0 else None
    except (TypeError, ValueError, OverflowError):
        return None


def parse_metrics(output):
    decoder = json.JSONDecoder()
    records = []
    offset = 0
    while offset < len(output):
        match = re.search(r"[\[{]", output[offset:])
        if not match:
            break
        start = offset + match.start()
        try:
            value, length = decoder.raw_decode(output[start:])
        except ValueError:
            offset = start + 1
            continue
        candidates = value if isinstance(value, list) else [value]
        records.extend(item for item in candidates if isinstance(item, dict) and "avg_ts" in item)
        offset = start + length
    metrics = {"benchmark_rows": records}
    cli_timings = re.findall(r"\[ Prompt:\s*([\d.]+)\s*t/s\s*\|\s*Generation:\s*([\d.]+)\s*t/s", output)
    if cli_timings:
        metrics.update(prompt_tps=_number(cli_timings[-1][0]), generation_tps=_number(cli_timings[-1][1]))
    for record in records:
        speed = _number(record.get("avg_ts"))
        if speed is None:
            continue
        if record.get("n_prompt", 0) > 0 and record.get("n_gen", 0) == 0:
            metrics["prompt_tps"] = speed
            metrics["prompt_stddev"] = _number(record.get("stddev_ts"))
        elif record.get("n_gen", 0) > 0 and record.get("n_prompt", 0) == 0:
            metrics["generation_tps"] = speed
            metrics["generation_stddev"] = _number(record.get("stddev_ts"))
    perplexities = re.findall(r"(?:PPL|perplexity)\s*(?:=|:)\s*([\d.eE+\-]+)", output, re.IGNORECASE)
    if perplexities:
        value = _number(perplexities[-1])
        if value is not None:
            metrics["perplexity"] = value
    return metrics


def explain_failure(output, exit_code):
    if exit_code == 0:
        return ""
    lower = output.lower()
    if "out of memory" in lower or "failed to allocate" in lower or "cuda error 2" in lower:
        return "Insufficient memory. Reduce GPU layers, batch/ubatch or context; inspect the allocation log."
    if "failed to load model" in lower or "error loading model" in lower:
        return "Model could not load. Check that this is the main GGUF and that the runtime supports its architecture/quantization."
    if "unknown argument" in lower or "invalid argument" in lower:
        return "This runtime rejected an option. Compare its --help output with the saved command."
    lines = [line for line in output.splitlines() if "error" in line.lower() or "failed" in line.lower()]
    return lines[-1] if lines else f"Runtime exited with code {exit_code}; inspect the raw output."
