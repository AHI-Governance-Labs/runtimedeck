"""Optional session persistence; applications retain their messages and sampling."""

from tkinter import ttk

from .widgets import ScrolledForm


class KVPanel(ttk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent)
        controller = app.kv
        form = ScrolledForm(self)
        form.pack(fill="both", expand=True)
        content = form.content
        content.columnconfigure(0, weight=1)
        ttk.Label(content, text="Conserva la KV de tus sesiones", style="Title.TLabel").grid(row=0, sticky="w")
        ttk.Label(content, text="Guarda y restaura la KV de un slot inactivo. El historial de mensajes y los parámetros "
                  "de generación se conservan en tu app cliente.", wraplength=650).grid(row=1, sticky="ew", pady=8)
        config = ttk.LabelFrame(content, text="Almacenamiento OTT · se aplica al iniciar el servidor", padding=10)
        config.grid(row=2, sticky="ew")
        config.columnconfigure(1, weight=1)
        ttk.Checkbutton(config, text="Habilitar sesiones KV", variable=app.kv_cache_enabled_var).grid(
            row=0, columnspan=3, sticky="w")
        for row, (label, key) in enumerate((("Carpeta ott-core", "kv_ott_core"),
                                          ("Carpeta de sesiones", "kv_cache_directory")), 1):
            ttk.Label(config, text=label).grid(row=row, column=0, sticky="w", padx=(0, 8))
            ttk.Entry(config, textvariable=getattr(app, key + "_var")).grid(row=row, column=1, sticky="ew", pady=3)
            ttk.Button(config, text="Elegir", command=lambda k=key: controller.choose_directory(k)).grid(
                row=row, column=2, padx=(8, 0))
        app._spin(config, "Presupuesto en disco (MiB)", app.kv_cache_budget_mib_var, 3, 64, 1048576)
        app._spin(config, "Límite por sesión (MiB)", app.kv_cache_max_mib_var, 4, 1, 4096)
        ttk.Label(config, text="OTT requiere numpy. Puedes indicar su repositorio o dejar la ruta vacía si está "
                  "instalado en este Python. Se conservan dos réplicas por copia.",
                  wraplength=640, style="Muted.TLabel").grid(row=5, columnspan=3, sticky="ew", pady=6)
        actions = ttk.Frame(content)
        actions.grid(row=3, sticky="ew", pady=10)
        ttk.Label(actions, text="Slot").pack(side="left")
        ttk.Spinbox(actions, from_=0, to=63, textvariable=controller.slot, width=4).pack(side="left", padx=6)
        ttk.Entry(actions, textvariable=controller.label, width=20).pack(side="left", padx=(0, 6))
        self.operation_buttons = []
        for label, command in (("Guardar slot", controller.save), ("Restaurar copia", controller.restore)):
            button = ttk.Button(actions, text=label, command=command)
            button.pack(side="left", padx=3)
            self.operation_buttons.append(button)
        ttk.Button(actions, text="Actualizar", command=controller.refresh).pack(side="left", padx=3)
        self.snapshots = ttk.Treeview(content, columns=("date", "tokens", "size"), selectmode="browse",
                                     show="tree headings", height=6)
        self.snapshots.heading("#0", text="Sesión")
        self.snapshots.heading("date", text="Guardada (UTC)")
        self.snapshots.heading("tokens", text="Tokens KV")
        self.snapshots.heading("size", text="KV nativa")
        self.snapshots.column("#0", width=180)
        self.snapshots.column("date", width=150)
        self.snapshots.column("tokens", width=80, anchor="e")
        self.snapshots.column("size", width=90, anchor="e")
        self.snapshots.grid(row=5, sticky="ew")
        ttk.Label(content, textvariable=controller.status, wraplength=650).grid(row=4, sticky="ew", pady=8)
        ttk.Label(content, text="Para reutilizar la KV, dirige la siguiente petición al mismo slot y reenvía el prefijo "
                  "original. Pausa las peticiones a ese slot mientras guardas o restauras. La restauración sustituye "
                  "su KV actual. Esta función conserva sesiones; ampliar el contexto activo requiere otra integración "
                  "dentro del runtime.", wraplength=650, style="Muted.TLabel").grid(row=6, sticky="ew")
