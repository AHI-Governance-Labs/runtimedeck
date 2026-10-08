"""Pure llama.cpp command builders; subprocess always receives an argv list."""

import os
import shlex
import subprocess
from pathlib import Path

from .settings import validate_value


def format_command(argv):
    return subprocess.list2cmdline(argv) if os.name == "nt" else shlex.join(argv)


def build_command(kind, model, runtime, values, dataset=""):
    capability = {"inference": "cli", "benchmark": "bench", "server": "server", "quality": "perplexity"}[kind]
    if model is None:
        raise ValueError("Select a model first.")
    if runtime is None:
        raise ValueError("Select a runtime first.")
    executable = getattr(runtime, capability)
    if not executable:
        raise ValueError(f"Selected runtime does not provide {capability}.")
    if model.ext.lower() != "gguf":
        raise ValueError("The llama.cpp adapter requires a GGUF model. Select a .gguf file.")
    if model.is_auxiliary:
        raise ValueError("This file is a multimodal projector (mmproj), not a language model. Select the main GGUF model.")
    if not Path(model.path).is_file() or not Path(executable).is_file():
        raise ValueError("The selected model or executable no longer exists. Rescan the workspace.")
    flags = {
        "inference": [("ctx", "-c"), ("ngl", "-ngl"), ("threads", "-t"),
                      ("batch", "-b"), ("ubatch", "-ub"), ("max_tokens", "-n"),
                      ("temp", "--temp"), ("top_p", "--top-p"), ("top_k", "--top-k"),
                      ("seed", "--seed"), ("prompt", "-p")],
        "benchmark": [("bench_prompt", "-p"), ("bench_gen", "-n"), ("bench_reps", "-r"),
                      ("ngl", "-ngl"), ("threads", "-t")],
        "server": [("server_host", "--host"), ("server_port", "--port"),
                   ("ctx", "-c"), ("ngl", "-ngl"), ("threads", "-t"),
                   ("batch", "-b"), ("ubatch", "-ub")],
        "quality": [("ctx", "-c"), ("ngl", "-ngl"), ("threads", "-t"),
                    ("batch", "-b"), ("ubatch", "-ub")],
    }
    argv = [executable, "-m", model.path]
    for key, flag in flags[kind]:
        argv.extend((flag, str(validate_value(key, values[key]))))
    if kind == "benchmark":
        argv.extend(("-o", "json", "-b", str(validate_value("batch", values["batch"])),
                     "-ub", str(validate_value("ubatch", values["ubatch"])),
                     "-fa", "on" if values["flash"] else "off"))
    if kind == "inference":
        argv.extend(("--single-turn", "--simple-io", "--perf"))
    if kind == "server":
        argv.extend(("--alias", model.name, "--parallel", str(validate_value("server_parallel", values.get("server_parallel", 1)))))
    if kind == "quality":
        if not dataset or not Path(dataset).is_file():
            raise ValueError("Select an existing text corpus for perplexity.")
        chunks = int(values.get("quality_chunks", 2))
        if not 1 <= chunks <= 1000:
            raise ValueError("Quality chunks must be between 1 and 1000.")
        argv.extend(("-f", dataset, "--chunks", str(chunks)))
    if kind != "benchmark" and validate_value("flash", values["flash"]):
        argv.extend(("--flash-attn", "on"))
    if kind in {"inference", "server"} and not validate_value("mmap", values["mmap"]):
        argv.append("--no-mmap")
    return argv
