"""Server-first connection panel and startup resource controls."""

from tkinter import ttk

from .widgets import ScrolledForm


class ServerPanel(ttk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent)
        self.pack(fill="both", expand=True)
        form = ScrolledForm(self)
        form.pack(fill="both", expand=True)
        content = form.content
        content.columnconfigure(0, weight=1)
        connection = app.server_connection

        ttk.Label(content, text="Conecta tus aplicaciones", style="Title.TLabel").grid(row=0, column=0, sticky="w", pady=(0, 4))
        ttk.Label(content, text="Carga un modelo aquí y usa su API desde tu editor, agente o interfaz de chat.",
                  wraplength=650, style="Muted.TLabel").grid(row=1, column=0, sticky="w", pady=(0, 12))

        published = ttk.LabelFrame(content, text="Conexión compatible con OpenAI", padding=12)
        published.grid(row=2, column=0, sticky="ew")
        published.columnconfigure(1, weight=1)
        ttk.Label(published, text="URL base").grid(row=0, column=0, sticky="w", padx=(0, 10))
        ttk.Entry(published, textvariable=connection.base_url, state="readonly").grid(row=0, column=1, sticky="ew", pady=3)
        ttk.Button(published, text="Copiar URL", command=lambda: connection.copy("base_url")).grid(row=0, column=2, padx=(8, 0))
        ttk.Label(published, text="ID del modelo").grid(row=1, column=0, sticky="w", padx=(0, 10))
        self.model_selector = ttk.Combobox(published, textvariable=connection.model_id, state="readonly")
        self.model_selector.grid(row=1, column=1, sticky="ew", pady=3)
        ttk.Button(published, text="Copiar ID", command=lambda: connection.copy("model")).grid(row=1, column=2, padx=(8, 0))
        ttk.Label(published, textvariable=connection.status, wraplength=640, style="Muted.TLabel").grid(row=2, column=0, columnspan=3, sticky="w", pady=8)
        actions = ttk.Frame(published)
        actions.grid(row=3, column=0, columnspan=3, sticky="w")
        ttk.Button(actions, text="Verificar API", command=connection.refresh).pack(side="left")
        ttk.Button(actions, text="Copiar conexión", command=lambda: connection.copy("config")).pack(side="left", padx=8)

        buttons = ttk.Frame(content)
        buttons.grid(row=3, column=0, sticky="w", pady=12)
        app._action_button(buttons, "INICIAR SERVIDOR", app.run_server).pack(side="left")
        ttk.Button(buttons, text="Detener", command=app.stop_process).pack(side="left", padx=8)
        ttk.Button(buttons, text="Ver comando", command=app.preview_server).pack(side="left")
        ttk.Label(content, text="Los parámetros de generación se ajustan en la app cliente", style="Title.TLabel").grid(row=4, column=0, sticky="w", pady=(4, 4))
        ttk.Label(content, text="Temperatura, top-p, top-k, semilla, tokens de salida, mensajes, instrucciones y streaming "
                  "viajan en cada petición. Las opciones disponibles dependen de tu app, del modelo y de la API del runtime. "
                  "Si la app omite una opción, se usa el valor predeterminado del runtime.",
                  wraplength=680).grid(row=5, column=0, sticky="ew", pady=(0, 12))

        resource = ttk.LabelFrame(content, text="Recursos del servidor · requieren reiniciar para aplicar cambios", padding=12)
        resource.grid(row=6, column=0, sticky="ew")
        resource.columnconfigure(1, weight=1)
        ttk.Label(resource, text="Host").grid(row=0, column=0, sticky="w")
        ttk.Entry(resource, textvariable=app.server_host_var).grid(row=0, column=1, sticky="ew", pady=2)
        for row, (label, variable, low, high) in enumerate((
            ("Puerto", app.server_port_var, 1, 65535),
            ("Contexto total (tokens)", app.ctx_var, 256, 1048576),
            ("Peticiones simultáneas", app.server_parallel_var, 1, 64),
            ("Capas GPU", app.ngl_var, 0, 999),
            ("Hilos CPU", app.threads_var, 1, 256),
            ("Batch", app.batch_var, 1, 8192),
            ("µBatch", app.ubatch_var, 1, 8192),
        ), 1):
            app._spin(resource, label, variable, row, low, high)
        ttk.Checkbutton(resource, text="Flash Attention", variable=app.flash_var).grid(row=8, column=0, columnspan=2, sticky="w")
        ttk.Checkbutton(resource, text="Memory map", variable=app.mmap_var).grid(row=9, column=0, columnspan=2, sticky="w")
        ttk.Label(resource, text="El contexto disponible por petición depende del runtime y de las peticiones simultáneas. "
                  "La app cliente puede usar hasta ese límite. Mantén RuntimeDeck abierto mientras uses el servidor.",
                  wraplength=640, style="Muted.TLabel").grid(row=10, column=0, columnspan=2, sticky="w", pady=8)
