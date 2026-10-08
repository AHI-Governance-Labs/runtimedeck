"""Filesystem discovery, independent of Tk and application state."""

import os
from pathlib import Path

from .config import APP_ROOT, MODEL_EXTS, MODEL_FOLDERS, RUNTIME_EXES, RUNTIME_FOLDERS
from .models import ModelItem, RuntimeItem

SKIP_FOLDERS = {".git", ".venv", "venv", "node_modules", "__pycache__", ".pytest_cache"}


def workspace_folders(root):
    """Resolve named asset folders without walking an entire drive."""
    children = sorted((path for path in Path(root).iterdir() if path.is_dir()), key=lambda p: p.name.casefold())
    return ([path for path in children if path.name.casefold() in MODEL_FOLDERS],
            [path for path in children if path.name.casefold() in RUNTIME_FOLDERS])


def normalize_workspace(root):
    """Accept the shared parent, an asset folder, or this application folder."""
    root = Path(root).expanduser().resolve()
    if root.is_dir() and (root.name.casefold() in MODEL_FOLDERS | RUNTIME_FOLDERS or root == APP_ROOT):
        models, runtimes = workspace_folders(root.parent)
        if models and runtimes:
            return root.parent
    return root


def _files(directory, warnings):
    def failed(exc):
        warnings.append(f"Cannot scan {exc.filename}: {exc.strerror}")
    for parent, folders, files in os.walk(directory, onerror=failed, followlinks=False):
        folders[:] = sorted(name for name in folders if name.casefold() not in SKIP_FOLDERS
                            and not (Path(parent) / name).is_symlink())
        for name in sorted(files):
            yield Path(parent) / name


def discover_workspace(root: Path):
    root = normalize_workspace(root)
    if not root.is_dir():
        raise ValueError(f"Workspace does not exist: {root}")
    models = []
    by_directory = {}
    warnings = []
    model_roots, runtime_roots = workspace_folders(root)
    if not model_roots:
        warnings.append(f"No models/Modelos folder found in {root}")
    if not runtime_roots:
        warnings.append(f"No runtimes folder found in {root}")
    seen_models = set()
    for folder in model_roots:
        for path in _files(folder, warnings):
            if path.suffix.lower() not in MODEL_EXTS or not path.is_file():
                continue
            try:
                resolved = path.resolve()
                if resolved in seen_models:
                    continue
                size = path.stat().st_size
            except OSError as exc:
                warnings.append(f"Cannot read {path}: {exc}")
                continue
            seen_models.add(resolved)
            models.append(ModelItem(str(path), path.name, path.suffix.lower().lstrip("."), size))
    for folder in runtime_roots:
        for path in _files(folder, warnings):
            capability = RUNTIME_EXES.get(path.name.lower())
            if capability is None or not path.is_file():
                continue
            directory = str(path.parent)
            label = path.parent.relative_to(folder).as_posix() if path.parent != folder else folder.name
            item = by_directory.setdefault(directory, RuntimeItem(directory, label))
            setattr(item, capability, str(path))
        # Source checkouts and other engines remain visible, with no launch capability.
        for child in sorted(folder.iterdir()):
            if not child.is_dir() or child.name.startswith(".") or child.name.casefold() in SKIP_FOLDERS or child.is_symlink():
                continue
            if not any(Path(directory).is_relative_to(child) for directory in by_directory):
                by_directory[str(child)] = RuntimeItem(str(child), child.name)
    models.sort(key=lambda model: (not model.runnable, model.name.lower()))
    runtimes = sorted(by_directory.values(), key=lambda item: (not bool(item.server), item.label.casefold()))
    return models, runtimes, warnings
