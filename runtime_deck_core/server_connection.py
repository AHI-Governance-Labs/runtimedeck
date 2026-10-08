"""Published API connection; generation options belong to each consuming client."""

import ipaddress
import json
import threading
import time
import tkinter as tk
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .settings import validate_value


def server_base_url(values):
    host = validate_value("server_host", values["server_host"]).strip()
    if host.startswith("[") and host.endswith("]"):
        host = host[1:-1]
    host = {"0.0.0.0": "127.0.0.1", "::": "::1"}.get(host, host)
    if any(char in host for char in "/?#@ \t\r\n"):
        raise ValueError("Host: indica un nombre o una dirección IP sin http:// ni puerto.")
    if ":" in host and not host.startswith("["):
        ipaddress.IPv6Address(host)
        host = f"[{host}]"
    port = validate_value("server_port", values["server_port"])
    return f"http://{host}:{port}/v1"


def fetch_model_ids(base_url, timeout=2):
    """Require the server's actual published inventory, with bounded I/O."""
    request = Request(base_url.rstrip("/") + "/models", headers={"Accept": "application/json"})
    with urlopen(request, timeout=timeout) as response:
        raw = response.read(1024 * 1024 + 1)
    if len(raw) > 1024 * 1024:
        raise ValueError("La lista de modelos de la API supera 1 MiB.")
    data = json.loads(raw)
    if not isinstance(data, dict) or not isinstance(data.get("data"), list):
        raise ValueError("La API no publica una lista compatible en /v1/models.")
    ids = list(dict.fromkeys(item["id"] for item in data["data"]
                            if isinstance(item, dict) and isinstance(item.get("id"), str) and item["id"].strip()))
    if not ids:
        raise ValueError("La API todavía no publica ningún modelo.")
    return ids


class ServerConnection:
    def __init__(self, app):
        self.app = app
        self.base_url = tk.StringVar(app, "")
        self.model_id = tk.StringVar(app, "")
        self.status = tk.StringVar(app, "Inicia el servidor o verifica una API existente.")
        self.model_ids = []
        self.generation = 0
        self.cancel = threading.Event()
        self.thread = None
        app.server_host_var.trace_add("write", self.address_changed)
        app.server_port_var.trace_add("write", self.address_changed)
        self.address_changed()

    def _address(self):
        values = self.app.server_values if self.app.active_kind == "server" else {
            "server_host": self.app.server_host_var.get(),
            "server_port": self.app.getvar(self.app.server_port_var._name),
        }
        return server_base_url(values)

    def _clear_models(self):
        self.model_ids = []
        self.model_id.set("")
        if hasattr(self.app, "server_panel"):
            self.app.server_panel.model_selector.configure(values=())

    def address_changed(self, *_):
        if self.app.active_kind == "server":
            return  # The running process owns its captured address until restart.
        self.close()
        self._clear_models()
        try:
            self.base_url.set(self._address())
            self.status.set("Inicia el servidor o verifica una API existente.")
        except (ValueError, tk.TclError) as exc:
            self.base_url.set("")
            self.status.set(str(exc))

    def refresh(self, wait_for_start=False):
        self.close()
        self._clear_models()
        try:
            base_url = self._address()
        except (ValueError, tk.TclError) as exc:
            self.status.set(str(exc))
            return
        self.base_url.set(base_url)
        self.status.set("Cargando modelo; esperando API…" if wait_for_start else "Verificando /v1/models…")
        self.cancel = threading.Event()
        self.thread = threading.Thread(target=self._probe, args=(
            self.generation, base_url, self.cancel, wait_for_start), daemon=True)
        self.thread.start()

    def _probe(self, generation, base_url, cancel, wait_for_start):
        deadline = time.monotonic() + (180 if wait_for_start else 0)
        while not cancel.is_set():
            try:
                ids = fetch_model_ids(base_url)
                if not cancel.is_set():
                    self.app.msgq.put(("server_connection", (generation, base_url, ids, "")))
                return
            except (OSError, URLError, ValueError) as exc:
                error = f"HTTP {exc.code}" if isinstance(exc, HTTPError) else str(exc)
                if not wait_for_start or time.monotonic() >= deadline:
                    if not cancel.is_set():
                        self.app.msgq.put(("server_connection", (generation, base_url, [], error)))
                    return
                cancel.wait(0.5)

    def event(self, payload):
        generation, base_url, ids, error = payload
        if generation != self.generation or base_url != self.base_url.get():
            return
        if error:
            self.status.set(f"API no disponible: {error}")
            return
        self.model_ids = ids
        self.model_id.set(ids[0])
        self.app.server_panel.model_selector.configure(values=ids)
        self.status.set(f"API lista · {len(ids)} modelo(s) publicado(s) · parámetros por petición")

    def copy(self, kind):
        if not self.model_ids or self.model_id.get() not in self.model_ids:
            self.status.set("Verifica la API antes de copiar la conexión.")
            return
        config = {"base_url": self.base_url.get(), "model": self.model_id.get()}
        value = json.dumps(config, ensure_ascii=False, indent=2) if kind == "config" else config[kind]
        self.app.clipboard_clear()
        self.app.clipboard_append(value)
        self.status.set("Conexión copiada. Ajusta la generación en tu app cliente.")

    def stopped(self, exit_code=0):
        self.address_changed()
        self.status.set(f"El servidor terminó con código {exit_code}. Revisa Salida y diagnóstico."
                        if exit_code else "Servidor detenido. Inícialo para conectar otra app.")

    def close(self):
        self.cancel.set()
        self.generation += 1
