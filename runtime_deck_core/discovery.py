"""Filesystem discovery, independent of Tk and application state."""

from pathlib import Path

from .config import MODEL_EXTS, RUNTIME_EXES
from .models import ModelItem, RuntimeItem


def discover_workspace(root: Path):
    root = Path(root)
    if not root.is_dir():
        raise ValueError(f"Workspace does not exist: {root}")
    models = []
    by_directory = {}
    warnings = []
    for path in sorted((root / "models").rglob("*")):
        if path.suffix.lower() not in MODEL_EXTS or not path.is_file():
            continue
        try:
            size = path.stat().st_size
        except OSError as exc:
            warnings.append(f"Cannot read {path}: {exc}")
            continue
        models.append(ModelItem(str(path), path.name, path.suffix.lower().lstrip("."), size))
    for path in sorted((root / "runtimes").rglob("*")):
        capability = RUNTIME_EXES.get(path.name.lower())
        if capability is None or not path.is_file():
            continue
        directory = str(path.parent)
        item = by_directory.setdefault(directory, RuntimeItem(directory, path.parent.name))
        setattr(item, capability, str(path))
    models.sort(key=lambda model: (not model.runnable, model.name.lower()))
    return models, list(by_directory.values()), warnings
