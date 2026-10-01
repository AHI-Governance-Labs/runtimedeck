"""Readable inventory labels; filenames and model files remain untouched."""

import re
from pathlib import Path


def model_label(model, root):
    stem = Path(model.name).stem
    quant = re.search(r"(?:[-_])((?:I?Q|PQ)\d[\w]*|BF16|F16|F32)$", stem, re.I)
    quantization = quant.group(1).upper() if quant else "—"
    if quant:
        stem = stem[:quant.start()]
    stem = re.sub(r"(?:[-_])(?:mmproj|projector)", "", stem, flags=re.I)
    stem = re.sub(r"^Ternary[-_]", "", stem, flags=re.I)
    parameters = re.search(r"(?:^|[-_])(\d+(?:\.\d+)?B)(?:$|[-_])", stem, re.I)
    scale = parameters.group(1).upper() if parameters else "—"
    title = re.sub(r"[-_]", " ", stem).strip()
    try:
        parts = Path(model.path).relative_to(Path(root) / "models").parts
        folder = parts[0] if len(parts) > 1 else ""
    except ValueError:
        folder = ""
    family = folder.title() if folder and not re.fullmatch(r"\d+(?:\.\d+)?B", folder, re.I) else title.split()[0]
    return {"title": title, "family": family, "scale": scale, "quantization": quantization,
            "role": "Auxiliares" if model.is_auxiliary else "Modelos" if model.runnable else "Otros formatos"}
