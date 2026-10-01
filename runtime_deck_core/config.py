"""Application constants, without filesystem side effects."""

import os
from pathlib import Path

APP_NAME = "RuntimeDeck"
DEFAULT_ROOT = Path(r"G:\Runtimes")
MODEL_EXTS = {".gguf", ".safetensors", ".bin", ".onnx", ".pt", ".pth"}
RUNTIME_EXES = {
    "llama-cli.exe": "cli",
    "llama-bench.exe": "bench",
    "llama-server.exe": "server",
    "llama-perplexity.exe": "perplexity",
    "llama-quantize.exe": "quantize",
}
CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0
SETTINGS_PATH = Path(os.environ.get("APPDATA", str(Path.home()))) / APP_NAME / "settings.json"
