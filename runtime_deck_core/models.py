"""Inventory records and display formatting."""
from dataclasses import dataclass


def human_size(n: int) -> str:
    units = ["B", "KiB", "MiB", "GiB", "TiB"]
    x = float(n)
    for u in units:
        if x < 1024 or u == units[-1]:
            return f"{x:.2f} {u}"
        x /= 1024
    return f"{n} B"


@dataclass
class ModelItem:
    path: str
    name: str
    ext: str
    size: int

    @property
    def is_auxiliary(self) -> bool:
        """Filename hint for multimodal projectors; not a claim about GGUF metadata."""
        name = self.name.lower()
        return "mmproj" in name or "projector" in name

    @property
    def runnable(self) -> bool:
        return self.ext.lower() == "gguf" and not self.is_auxiliary


@dataclass
class RuntimeItem:
    directory: str
    label: str
    cli: str = ""
    bench: str = ""
    server: str = ""
    perplexity: str = ""
    quantize: str = ""

    @property
    def capabilities(self) -> str:
        caps = []
        if self.cli:
            caps.append("infer")
        if self.bench:
            caps.append("bench")
        if self.server:
            caps.append("server")
        if self.perplexity:
            caps.append("ppl")
        if self.quantize:
            caps.append("quant")
        return ", ".join(caps) or "unknown"
