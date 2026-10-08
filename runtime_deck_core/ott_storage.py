"""Optional OTT v0.3 storage for immutable byte snapshots; no GPU dependency."""

import hashlib
import importlib
import importlib.util
import os
import sys
import threading
from pathlib import Path

_import_lock = threading.Lock()


def load_ott(core_path=""):
    """Load a configured checkout without changing the application's sys.path."""
    with _import_lock:
        try:
            if core_path:
                entry = Path(core_path).expanduser().resolve() / "ott" / "__init__.py"
                if not entry.is_file():
                    raise ValueError("Selecciona la raíz de ott-core, que contiene ott/__init__.py.")
                name = "_runtime_deck_ott_" + hashlib.sha256(str(entry).encode()).hexdigest()[:16]
                if name not in sys.modules:
                    spec = importlib.util.spec_from_file_location(name, entry,
                                                                 submodule_search_locations=[str(entry.parent)])
                    module = importlib.util.module_from_spec(spec)
                    sys.modules[name] = module
                    try:
                        spec.loader.exec_module(module)
                    except Exception:
                        sys.modules.pop(name, None)
                        raise
                package = sys.modules[name]
            else:
                package = importlib.import_module("ott")
            return importlib.import_module(package.__name__ + ".ott_tensor_v03")
        except (ImportError, AttributeError) as exc:
            raise ValueError("OTT no está disponible. Configura ott-core e instala su dependencia numpy "
                             "en el Python que ejecuta RuntimeDeck.") from exc


class OttSnapshotStorage:
    def __init__(self, core_path=""):
        self.core_path = core_path

    def store(self, path, blob):
        module = load_ott(self.core_path)
        import numpy as np  # Optional: only imported when this feature is used.

        mind = module.OTTMind(Path(path), backend="cpu")
        mind.initialize(module.DATA_OFFSET + 2 * len(blob) + 4096)
        mind.put_tensor("0", np.frombuffer(blob, dtype=np.uint8), compression="none", replicas=2,
                        metadata={"kind": "runtime-deck-kv-snapshot", "immutable": True})
        mind.verify_tensor("0")
        status = mind.status()
        with Path(path).open("r+b") as stream:
            stream.flush()
            os.fsync(stream.fileno())
        return {"audit_chain": status["audit_chain"], "replicas": 2, "metrics": mind.metrics.copy()}

    def load(self, path, max_bytes):
        module = load_ott(self.core_path)
        path = Path(path)
        # Bound allocations before calling the tensor API, including damaged manifests.
        with path.open("rb") as stream:
            header = module.parse_header(stream.read(module.HEADER_SIZE))
            if (header["manifest_offset"], header["manifest_size"], header["audit_offset"],
                    header["audit_size"], header["data_offset"]) != (
                    module.MANIFEST_OFFSET, module.MANIFEST_SIZE, module.AUDIT_OFFSET,
                    module.AUDIT_SIZE, module.DATA_OFFSET):
                raise ValueError("El volumen OTT tiene un diseño incompatible.")
            manifest = module.read_json_block(stream, module.MANIFEST_OFFSET, module.MANIFEST_SIZE, {})
        entry = manifest.get("regions", {}).get("0", {})
        length = entry.get("raw_length", 0)
        if (not isinstance(length, int) or not 0 < length <= max_bytes or
                entry.get("dtype") != "uint8" or entry.get("shape") != [length] or
                entry.get("compression") != "none" or entry.get("stored_length") != length):
            raise ValueError("El tensor OTT supera el límite o no es una copia KV compatible.")
        copies = [entry] + entry.get("replicas", [])
        if len(copies) != 2:
            raise ValueError("La copia KV debe conservar dos réplicas OTT.")
        for copy in copies:
            offset = copy.get("offset", -1)
            if (not isinstance(offset, int) or offset < module.DATA_OFFSET or
                    copy.get("stored_length") != length or offset + length > path.stat().st_size):
                raise ValueError("Los límites del tensor OTT están dañados.")
        mind = module.OTTMind(path, backend="cpu")
        mind.status()  # Validate the audit chain before serving any bytes.
        blob = mind.prefetch("0").tobytes()
        status = mind.status()
        return blob, {"audit_chain": status["audit_chain"], "metrics": mind.metrics.copy()}
