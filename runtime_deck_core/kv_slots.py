"""llama.cpp native slot adapter; snapshots never reinterpret its KV layout."""

import json
import os
import uuid
from urllib.error import HTTPError
from urllib.request import Request, urlopen


def slot_json(origin, route, body=None):
    request = Request(origin + route, data=None if body is None else json.dumps(body).encode("utf-8"),
                      headers={"Accept": "application/json", "Content-Type": "application/json"})
    try:
        with urlopen(request, timeout=45) as response:
            raw = response.read(2 * 1024 * 1024 + 1)
    except HTTPError as exc:
        detail = exc.read(4096).decode("utf-8", errors="replace")
        raise ValueError(f"API de slots: HTTP {exc.code}: {detail}") from exc
    if len(raw) > 2 * 1024 * 1024:
        raise ValueError("La respuesta de slots supera 2 MiB.")
    return json.loads(raw)


class SlotKVService:
    def __init__(self, base_url, archive, identity, guard=lambda: None):
        self.origin = base_url.rstrip("/").removesuffix("/v1")
        self.archive = archive
        self.identity = identity
        self.guard = guard

    def idle_slot(self, slot_id):
        self.guard()
        if isinstance(slot_id, bool) or not isinstance(slot_id, int) or slot_id < 0:
            raise ValueError("El slot debe ser un entero no negativo.")
        slots = slot_json(self.origin, "/slots")
        if not isinstance(slots, list):
            raise ValueError("El runtime no publica una lista compatible de slots.")
        slot = next((item for item in slots if isinstance(item, dict) and item.get("id") == slot_id), None)
        if slot is None:
            raise ValueError("Ese slot no existe en el servidor activo.")
        if slot.get("is_processing") is not False:
            raise ValueError("El slot está ocupado. Espera a que termine la petición de tu app.")
        return slot

    def _action(self, slot_id, action, filename=None):
        self.idle_slot(slot_id)
        self.guard()
        result = slot_json(self.origin, f"/slots/{slot_id}?action={action}",
                           {} if filename is None else {"filename": filename})
        if not isinstance(result, dict) or result.get("id_slot") != slot_id or "error" in result:
            raise ValueError(f"El runtime no confirmó la operación KV: {result}")
        return result

    def save(self, slot_id, label):
        self.idle_slot(slot_id)
        self.archive.require_space(9 * 1024 * 1024)
        self.archive.slots_path.mkdir(parents=True, exist_ok=True)
        filename = "rdkv-" + uuid.uuid4().hex + ".bin"
        native = self.archive.slots_path / filename
        try:
            result = self._action(slot_id, "save", filename)
            if result.get("n_saved", 0) <= 0 or result.get("n_written", 0) != native.stat().st_size:
                raise ValueError("El runtime no guardó una KV válida; procesa primero un prompt en ese slot.")
            self.guard()
            evidence = self.archive.save(native, self.identity, label, result["n_saved"])
            return dict(evidence, runtime=result)
        finally:
            native.unlink(missing_ok=True)

    def restore(self, slot_id, snapshot_id):
        payload, evidence = self.archive.restore_bytes(snapshot_id, self.identity)
        self.archive.require_space(len(payload))
        self.archive.slots_path.mkdir(parents=True, exist_ok=True)
        filename = "rdkv-" + uuid.uuid4().hex + ".bin"
        native = self.archive.slots_path / filename
        try:
            with native.open("xb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            result = self._action(slot_id, "restore", filename)
            if result.get("n_restored") != evidence["snapshot"]["tokens"] or result.get("n_read") != len(payload):
                raise ValueError(f"El runtime restauró una cantidad inesperada de KV: {result}")
            return dict(evidence, runtime=result)
        finally:
            native.unlink(missing_ok=True)
