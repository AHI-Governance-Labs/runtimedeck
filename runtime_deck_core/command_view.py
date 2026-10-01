"""Interactive command snapshots: inspect argv, copy, restore and rebuild."""

import copy
import json
import tkinter as tk
from datetime import datetime
from tkinter import messagebox, ttk

from .commands import build_command, format_command
from .settings import validate_settings
from .theme import style_text


class CommandPanel(ttk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent, padding=6)
        self.app = app
        self.records = []
        self.current = None
        self.columnconfigure(1, weight=1)
        self.rowconfigure(1, weight=1)
        toolbar = ttk.Frame(self)
        toolbar.grid(row=0, column=0, columnspan=3, sticky="ew", pady=(0, 4))
        ttk.Button(toolbar, text="Copiar comando", command=self.copy_command).pack(side="left")
        ttk.Button(toolbar, text="Aplicar JSON y reconstruir", command=self.restore).pack(side="left", padx=5)
        ttk.Button(toolbar, text="Ejecutar reconstruido", command=self.run_restored).pack(side="left")
        self.state = tk.StringVar(self, "Selecciona una ejecución o usa Vista previa")
        ttk.Label(toolbar, textvariable=self.state, style="Muted.TLabel").pack(side="right")
        self.history = ttk.Treeview(self, show="tree", height=4, selectmode="browse")
        self.history.column("#0", width=220, stretch=False)
        self.history.grid(row=1, column=0, sticky="nsew", padx=(0, 6))
        self.history.bind("<<TreeviewSelect>>", self._selected)
        notebook = ttk.Notebook(self)
        notebook.grid(row=1, column=1, sticky="nsew")
        arguments = ttk.Frame(notebook)
        settings = ttk.Frame(notebook)
        command = ttk.Frame(notebook)
        notebook.add(arguments, text="Argumentos")
        notebook.add(settings, text="Parámetros JSON · editable")
        notebook.add(command, text="Comando · seleccionable")
        self.arguments = app._scrolled(arguments, ttk.Treeview, columns=("value",), show="tree headings", height=4)
        self.arguments.heading("#0", text="Argumento")
        self.arguments.heading("value", text="Valor capturado")
        self.arguments.column("#0", width=110, stretch=False)
        self.arguments.column("value", width=400)
        self.editor = app._scrolled(settings, tk.Text, height=4, wrap="word", undo=True)
        style_text(self.editor)
        self.command = app._scrolled(command, tk.Text, height=4, wrap="word")
        style_text(self.command)
        self.command.configure(state="disabled")

    def record(self, argv, kind, model, runtime, values, source="Vista previa"):
        snapshot = {"argv": list(argv), "kind": kind, "model": copy.deepcopy(model), "runtime": copy.deepcopy(runtime),
                    "settings": copy.deepcopy(values), "time": datetime.now().strftime("%H:%M:%S"), "state": source}
        self.records.append(snapshot)
        if len(self.records) > 100:
            self.records.pop(0)
        self.history.delete(*self.history.get_children())
        for index, item in enumerate(self.records):
            self.history.insert("", 0, iid=f"c{index}", text=f"{item['time']} · {item['kind']} · {item['state']}")
        self.history.selection_set(f"c{len(self.records) - 1}")
        self.show(snapshot)
        return snapshot

    def _selected(self, event=None):
        selection = self.history.selection()
        if selection:
            self.show(self.records[int(selection[0][1:])])

    def show(self, snapshot):
        self.current = snapshot
        self.state.set(f"{snapshot['state']} · {snapshot['model'].get('name', '')}")
        self.arguments.delete(*self.arguments.get_children())
        argv = snapshot["argv"]
        self.arguments.insert("", "end", text="Ejecutable", values=(argv[0],))
        index = 1
        while index < len(argv):
            flag = argv[index]
            value = ""
            if index + 1 < len(argv) and (not argv[index + 1].startswith("-") or argv[index + 1].lstrip("-").replace(".", "", 1).isdigit()):
                value = argv[index + 1]
                index += 1
            self.arguments.insert("", "end", text=flag, values=(value,))
            index += 1
        self.editor.delete("1.0", "end")
        self.editor.insert("1.0", json.dumps(snapshot["settings"], indent=2, ensure_ascii=False))
        self.command.configure(state="normal")
        self.command.delete("1.0", "end")
        self.command.insert("1.0", format_command(argv))
        self.command.configure(state="disabled")

    def copy_command(self):
        if self.current:
            self.clipboard_clear()
            self.clipboard_append(format_command(self.current["argv"]))
            self.state.set("Comando copiado")

    def restore(self):
        if not self.current:
            return False
        if self.app.is_busy():
            messagebox.showwarning("Comando", "Detén la tarea activa antes de restaurar parámetros")
            return False
        try:
            values = json.loads(self.editor.get("1.0", "end"))
            if not isinstance(values, dict):
                raise ValueError("Se requiere un objeto JSON de parámetros")
            values = validate_settings(values)
            models = [model for model in self.app.models if model.path == self.current["model"].get("path")]
            runtimes = [runtime for runtime in self.app.runtimes if runtime.directory == self.current["runtime"].get("directory")]
            if len(models) != 1 or len(runtimes) != 1:
                raise ValueError("La selección capturada ya no está en el inventario. Escanea y revisa sus rutas")
            kind = self.current["kind"]
            if kind not in ("inference", "benchmark", "server"):
                raise ValueError("Este experimento se reconstruye desde Experiments")
            argv = build_command(kind, models[0], runtimes[0], values)
            self.app.select_inventory(models[0], runtimes[0])
            self.app.apply_configuration(values)
            self.record(argv, kind, self.current["model"], self.current["runtime"], values, "Reconstruido")
            self.app.command_var.set(format_command(argv))
            return True
        except (ValueError, OSError, KeyError) as exc:
            messagebox.showerror("Comando", str(exc))
            return False

    def run_restored(self):
        if self.restore():
            {"inference": self.app.run_inference, "benchmark": self.app.run_benchmark,
             "server": self.app.run_server}[self.current["kind"]]()

    def finish(self, snapshot, code):
        if snapshot is not None:
            snapshot["state"] = f"salida {code}"
            if snapshot in self.records:
                index = self.records.index(snapshot)
                self.history.item(f"c{index}", text=f"{snapshot['time']} · {snapshot['kind']} · {snapshot['state']}")
            if self.current is snapshot:
                self.state.set(f"salida {code} · {snapshot['model'].get('name', '')}")
