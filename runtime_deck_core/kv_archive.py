"""Versioned, immutable native KV snapshots with bounded storage and compatibility."""

import hashlib
import json
import os
import re
import shutil
import struct
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .ott_storage import OttSnapshotStorage

MIB = 1024 * 1024
MAGIC = b"RDKV001\0"
HEADER_LIMIT = 64 * 1024
OTT_OVERHEAD = 9 * MIB
ID_PATTERN = re.compile(r"[0-9a-f]{32}\Z")


def file_signature(path):
    stat = Path(path).stat()
    return (stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)


def fingerprint_file(path, expected=None):
    before = file_signature(path)
    if expected is not None and before != expected:
        raise ValueError("El modelo o runtime cambió después del arranque. Reinicia el servidor.")
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(4 * MIB), b""):
            digest.update(block)
    if file_signature(path) != before:
        raise ValueError("El archivo cambió mientras se calculaba su identidad.")
    return digest.hexdigest()


def server_identity(model, runtime, values, signatures=None):
    """Generation sampling belongs to the client and is excluded from KV identity."""
    signatures = signatures or (None, None, None)
    library_paths = sorted(Path(runtime.server).parent.glob("*.dll"))
    if signatures[2] is not None and {path.name: file_signature(path) for path in library_paths} != signatures[2]:
        raise ValueError("Las bibliotecas del runtime cambiaron después del arranque. Reinicia el servidor.")
    libraries = {path.name: fingerprint_file(path, None if signatures[2] is None else signatures[2][path.name])
                 for path in library_paths}
    return {"adapter": "llama.cpp-slots-v1", "model_sha256": fingerprint_file(model.path, signatures[0]),
            "runtime_sha256": fingerprint_file(runtime.server, signatures[1]),
            "runtime_libraries": libraries,
            "layout": {key: values[key] for key in ("ctx", "server_parallel", "flash")}}


def _write_json(path, value):
    temporary = path.with_suffix(".json.tmp")
    try:
        with temporary.open("w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


class KVArchive:
    def __init__(self, root, core_path="", budget_mib=4096, max_snapshot_mib=512, storage=None):
        self.root = Path(root).expanduser().resolve()
        self.budget = int(budget_mib) * MIB
        self.max_bytes = int(max_snapshot_mib) * MIB
        self.storage = storage or OttSnapshotStorage(core_path)

    @property
    def slots_path(self):
        return self.root / "slots"

    def used_bytes(self):
        files = list(self.root.glob("*.ott*")) + list(self.root.glob("*.json"))
        files += list(self.slots_path.glob("rdkv-*.bin"))
        return sum(path.stat().st_size for path in files)

    def require_space(self, bytes_needed):
        self.root.mkdir(parents=True, exist_ok=True)
        if self.used_bytes() + bytes_needed > self.budget:
            raise ValueError("El archivo KV supera el presupuesto de almacenamiento. Amplía el presupuesto "
                             "o retira copias antiguas de la carpeta configurada.")
        if shutil.disk_usage(self.root).free < bytes_needed + 64 * MIB:
            raise ValueError("No hay espacio libre suficiente para conservar la copia KV.")

    def _path(self, snapshot_id, suffix):
        if not isinstance(snapshot_id, str) or not ID_PATTERN.fullmatch(snapshot_id):
            raise ValueError("Identificador de copia KV inválido.")
        return self.root / (snapshot_id + suffix)

    def list_snapshots(self):
        records = []
        for path in self.root.glob("*.json"):
            if not ID_PATTERN.fullmatch(path.stem) or path.stat().st_size > HEADER_LIMIT:
                continue
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
                if not isinstance(record, dict):
                    continue
                if (record.get("format") == "runtime-deck-kv-v1" and record["id"] == path.stem and
                        isinstance(record.get("label"), str) and isinstance(record.get("created"), str) and
                        isinstance(record.get("bytes"), int) and record["bytes"] > 0 and
                        isinstance(record.get("tokens"), int) and record["tokens"] > 0 and
                        self._path(path.stem, ".ott").is_file()):
                    records.append(record)
            except (OSError, ValueError, KeyError, TypeError):
                continue
        return sorted(records, key=lambda item: item.get("created", ""), reverse=True)

    def save(self, native_path, identity, label, tokens):
        native_path = Path(native_path)
        size = native_path.stat().st_size
        if size <= 0:
            raise ValueError("La copia KV está vacía.")
        if size > self.max_bytes:
            raise ValueError(f"La copia KV pesa {size / MIB:.2f} MiB y supera el límite por sesión "
                             f"de {self.max_bytes / MIB:.0f} MiB. Amplía ese límite y reinicia el servidor.")
        self.require_space(2 * (size + HEADER_LIMIT) + OTT_OVERHEAD)
        payload = native_path.read_bytes()
        record = {"format": "runtime-deck-kv-v1", "id": uuid.uuid4().hex,
                  "created": datetime.now(timezone.utc).isoformat(), "label": label.strip()[:160] or "Sesión",
                  "tokens": int(tokens), "bytes": size, "sha256": hashlib.sha256(payload).hexdigest(),
                  "identity": identity}
        metadata = json.dumps(record, ensure_ascii=False, sort_keys=True, allow_nan=False).encode("utf-8")
        if len(metadata) > HEADER_LIMIT:
            raise ValueError("Los metadatos de la copia KV exceden el límite.")
        blob = MAGIC + struct.pack("<I", len(metadata)) + metadata + payload
        temporary = self._path(record["id"], ".ott.tmp")
        target = self._path(record["id"], ".ott")
        started = time.monotonic()
        try:
            evidence = self.storage.store(temporary, blob)
            os.replace(temporary, target)
            _write_json(self._path(record["id"], ".json"), record)
        except Exception:
            temporary.unlink(missing_ok=True)
            target.unlink(missing_ok=True)
            raise
        return {"snapshot": record, "storage": evidence, "archive_ms": (time.monotonic() - started) * 1000}

    def restore_bytes(self, snapshot_id, identity):
        catalog = self._path(snapshot_id, ".json")
        if catalog.stat().st_size > HEADER_LIMIT:
            raise ValueError("Los metadatos de la copia KV exceden el límite.")
        record = json.loads(catalog.read_text(encoding="utf-8"))
        if not isinstance(record, dict) or record.get("format") != "runtime-deck-kv-v1" or record.get("id") != snapshot_id:
            raise ValueError("La copia KV tiene un formato incompatible.")
        if record.get("identity") != identity:
            raise ValueError("Copia KV incompatible: deben coincidir el modelo, el binario del runtime "
                             "y la configuración de contexto.")
        if not isinstance(record.get("bytes"), int) or not 0 < record["bytes"] <= self.max_bytes:
            raise ValueError("La copia KV supera el límite por sesión configurado.")
        started = time.monotonic()
        blob, evidence = self.storage.load(self._path(snapshot_id, ".ott"), self.max_bytes + HEADER_LIMIT + 12)
        if len(blob) < 12 or blob[:8] != MAGIC:
            raise ValueError("El tensor no contiene una copia KV reconocida.")
        length = struct.unpack("<I", blob[8:12])[0]
        if not 0 < length <= HEADER_LIMIT or 12 + length >= len(blob):
            raise ValueError("La cabecera KV está dañada.")
        embedded = json.loads(blob[12:12 + length])
        if embedded != record:
            raise ValueError("El catálogo KV no coincide con los metadatos verificados por OTT.")
        payload = blob[12 + length:]
        if len(payload) != record["bytes"] or hashlib.sha256(payload).hexdigest() != record["sha256"]:
            raise ValueError("La copia KV no supera la verificación de integridad.")
        return payload, {"snapshot": record, "storage": evidence,
                         "archive_ms": (time.monotonic() - started) * 1000}
