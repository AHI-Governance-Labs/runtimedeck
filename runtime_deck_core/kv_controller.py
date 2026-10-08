"""Background KV operations bound to the owned server's captured configuration."""

import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog

from .kv_archive import KVArchive, file_signature, server_identity
from .kv_slots import SlotKVService, slot_json
from .models import human_size
from .server_connection import server_base_url


class KVController:
    def __init__(self, app):
        self.app = app
        self.status = tk.StringVar(app, "Activa las sesiones KV antes de iniciar el servidor.")
        self.label = tk.StringVar(app, "Sesión")
        self.slot = tk.IntVar(app, 0)
        self.thread = None
        self.pending = False
        self.cancel = threading.Event()
        self.generation = 0
        self.binding = None
        self.identity = None
        self.last_result = None
        self.records = []

    @property
    def running(self):
        return self.pending or (self.thread is not None and self.thread.is_alive())

    def bind(self, model, runtime, values):
        self.close()
        self.cancel = threading.Event()
        self.identity = None
        libraries = {path.name: file_signature(path) for path in Path(runtime.server).parent.glob("*.dll")}
        self.binding = (model, runtime, values.copy(), (file_signature(model.path), file_signature(runtime.server), libraries))
        self.last_result = None
        if values["kv_cache_enabled"]:
            archive = self._archive(values)
            archive.slots_path.mkdir(parents=True, exist_ok=True)
            self.status.set("Sesiones KV habilitadas. Espera a que la API esté lista.")
        else:
            self.status.set("Activa las sesiones KV y reinicia el servidor para guardar o restaurar.")
        self.refresh()

    def _values(self):
        return self.binding[2] if self.binding else self.app.collect_settings()

    @staticmethod
    def _archive(values):
        return KVArchive(values["kv_cache_directory"], values["kv_ott_core"], values["kv_cache_budget_mib"],
                         values["kv_cache_max_mib"])

    def choose_directory(self, key):
        variable = getattr(self.app, key + "_var")
        selected = filedialog.askdirectory(parent=self.app, initialdir=variable.get() or None)
        if selected:
            variable.set(selected)

    def refresh(self):
        try:
            self.records = self._archive(self._values()).list_snapshots()
            panel = self.app.kv_panel
            selected = panel.snapshots.selection()
            panel.snapshots.delete(*panel.snapshots.get_children())
            for record in self.records:
                panel.snapshots.insert("", "end", iid=record["id"], text=record.get("label", "Sesión"),
                                       values=(record.get("created", "")[:19].replace("T", " "),
                                               record.get("tokens", 0), human_size(record.get("bytes", 0))))
            if selected and panel.snapshots.exists(selected[0]):
                panel.snapshots.selection_set(selected[0])
        except (ValueError, OSError) as exc:
            self.status.set(str(exc))

    def save(self):
        self._start("save")

    def restore(self):
        selected = self.app.kv_panel.snapshots.selection()
        if not selected:
            self.status.set("Selecciona una copia guardada para restaurar.")
            return
        self._start("restore", selected[0])

    def _start(self, action, snapshot_id=None):
        if self.running:
            self.status.set("Hay una operación KV en curso.")
            return
        if (self.app.active_kind != "server" or not self.binding or not self.binding[2]["kv_cache_enabled"] or
                not self.app.server_connection.model_ids):
            self.status.set("Inicia un servidor con sesiones KV activadas y espera a que su API esté lista.")
            return
        try:
            raw_slot = self.app.getvar(self.slot._name)
            slot_id = int(raw_slot)
            if str(raw_slot) != str(slot_id) or not 0 <= slot_id < self.binding[2]["server_parallel"]:
                raise ValueError("Selecciona un slot entero dentro del número de peticiones simultáneas.")
        except (ValueError, tk.TclError) as exc:
            self.status.set(str(exc))
            return
        self.last_result = None
        self.pending = True
        self.generation += 1
        self.status.set("Verificando identidad del modelo y runtime; preparando KV…")
        binding, generation, cancel = self.binding, self.generation, self.cancel
        label = self.label.get()
        for button in self.app.kv_panel.operation_buttons:
            button.state(["disabled"])
        self.thread = threading.Thread(target=self._worker,
                                       args=(action, snapshot_id, slot_id, label, binding, generation, cancel), daemon=True)
        self.thread.start()

    def _worker(self, action, snapshot_id, slot_id, label, binding, generation, cancel):
        def guard():
            if cancel.is_set():
                raise ValueError("Operación KV cancelada porque el servidor terminó.")
        try:
            model, runtime, values, signatures = binding
            guard()
            if self.identity is None:
                identity = server_identity(model, runtime, values, signatures)
                slots = slot_json(server_base_url(values).removesuffix("/v1"), "/slots")
                identity["layout"]["slot_contexts"] = [(item["id"], item["n_ctx"]) for item in slots]
                # JSON-compatible containers are essential for persistence across processes.
                identity["layout"]["slot_contexts"] = [list(item) for item in identity["layout"]["slot_contexts"]]
                guard()
                self.identity = identity
            else:
                libraries = {path.name: file_signature(path) for path in Path(runtime.server).parent.glob("*.dll")}
                if (file_signature(model.path), file_signature(runtime.server), libraries) != signatures:
                    raise ValueError("El modelo o runtime cambió después del arranque. Reinicia el servidor.")
                identity = self.identity
            service = SlotKVService(server_base_url(values), self._archive(values), identity, guard)
            result = service.save(slot_id, label) if action == "save" else service.restore(slot_id, snapshot_id)
            guard()
            self.app.msgq.put(("kv_result", (generation, action, result, "")))
        except Exception as exc:
            self.app.msgq.put(("kv_result", (generation, action, None, str(exc))))

    def event(self, payload):
        generation, action, result, error = payload
        if generation != self.generation:
            return
        self.pending = False
        for button in self.app.kv_panel.operation_buttons:
            button.state(["!disabled"])
        self.last_result = result
        if error:
            self.status.set("KV: " + error)
            self.app.log.insert("end", "[kv] " + error + "\n")
            return
        record = result["snapshot"]
        verb = "guardada" if action == "save" else "restaurada"
        self.status.set(f"KV {verb} · {record['tokens']} tokens · {human_size(record['bytes'])} · auditoría OTT OK")
        self.refresh()
        self.app.kv_panel.snapshots.selection_set(record["id"])
        self.app.log.insert("end", f"[kv] {verb}: {record['id']} · {record['tokens']} tokens\n")

    def stopped(self):
        self.close()
        self.binding = None
        self.identity = None
        for button in self.app.kv_panel.operation_buttons:
            button.state(["!disabled"])
        self.status.set("Servidor detenido. Las copias KV siguen disponibles en disco.")

    def close(self):
        self.cancel.set()
        self.pending = False
        self.generation += 1
