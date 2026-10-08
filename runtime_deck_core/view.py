"""Tk layout and widgets; application actions are supplied by RuntimeDeck."""
import tkinter as tk
from tkinter import ttk
from .theme import apply_dark_theme, style_text
from .lab_view import LabPanel
from .chat_view import ChatPanel
from .monitor_view import MonitorPanel
from .telemetry_view import TelemetryStrip
from .prompt_view import PromptEditor
from .command_view import CommandPanel
from .plan_view import PlanPanel
from .widgets import ScrolledForm
from .server_view import ServerPanel
from .kv_view import KVPanel


class RuntimeDeckView(tk.Tk):
    def _build_ui(self):
        apply_dark_theme(self)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=1)

        top = ttk.Frame(self, padding=8)
        top.grid(row=0, column=0, sticky="ew")

        ttk.Label(top, text="RuntimeDeck", style="Title.TLabel").pack(side="left", padx=(0, 16))
        ttk.Label(top, text="Workspace:").pack(side="left")
        ttk.Entry(top, textvariable=self.root_var).pack(side="left", fill="x", expand=True, padx=6)
        ttk.Button(top, text="Browse", command=self.choose_root).pack(side="left", padx=2)
        ttk.Button(top, text="Rescan", command=self.scan).pack(side="left", padx=2)
        ttk.Button(top, text="Open folder", command=self.open_root).pack(side="left", padx=2)

        self.telemetry_strip = TelemetryStrip(self)
        self.telemetry_strip.grid(row=1, column=0, sticky="ew")

        self.workbench_pane = ttk.Panedwindow(self, orient="vertical")
        self.workbench_pane.grid(row=2, column=0, sticky="nsew", padx=8, pady=(0, 8))
        pane = ttk.Panedwindow(self.workbench_pane, orient="horizontal", height=480)
        self.workbench_pane.add(pane, weight=4)

        library = ttk.Panedwindow(pane, orient="vertical")
        left = ttk.Frame(library, padding=4)
        center = ttk.Frame(library, padding=4)
        right = ttk.Frame(pane, padding=4)
        library.add(left, weight=3)
        library.add(center, weight=2)
        pane.add(library, weight=0)
        pane.add(right, weight=4)

        # Models
        ttk.Label(left, text="MODELS", font=("Segoe UI", 10, "bold")).pack(anchor="w")
        ttk.Entry(left, textvariable=self.model_filter_var).pack(fill="x", pady=5)
        ttk.Label(left, text="Buscar por nombre, familia o cuantización", style="Muted.TLabel").pack(anchor="w")
        self.model_tree = self._scrolled(left, ttk.Treeview, columns=("quant", "size"), show="tree headings", selectmode="browse")
        self.model_tree.heading("#0", text="Name")
        self.model_tree.heading("size", text="Size")
        self.model_tree.heading("quant", text="Quant")
        self.model_tree.column("#0", width=185, minwidth=130)
        self.model_tree.column("size", width=75, anchor="e", stretch=False)
        self.model_tree.column("quant", width=65, stretch=False)
        self.model_tree.bind("<<TreeviewSelect>>", self.on_model_select)
        ttk.Label(left, textvariable=self.model_info_var, wraplength=380).pack(anchor="w", fill="x")

        # Runtimes
        ttk.Label(center, text="RUNTIMES", font=("Segoe UI", 10, "bold")).pack(anchor="w")
        self.runtime_tree = self._scrolled(center, ttk.Treeview, columns=("caps",), show="tree headings", selectmode="browse")
        self.runtime_tree.heading("#0", text="Runtime")
        self.runtime_tree.heading("caps", text="Capabilities")
        self.runtime_tree.column("#0", width=135)
        self.runtime_tree.column("caps", width=150)
        self.runtime_tree.bind("<<TreeviewSelect>>", self.on_runtime_select)
        ttk.Label(center, textvariable=self.runtime_info_var, wraplength=380).pack(anchor="w", fill="x")

        # Right notebook
        nb = ttk.Notebook(right)
        self.workspace_notebook = nb
        nb.pack(fill="both", expand=True)
        self.infer_tab = ttk.Frame(nb, padding=8)
        self.bench_tab = ttk.Frame(nb, padding=8)
        self.server_tab = ttk.Frame(nb, padding=8)
        self.system_tab = ttk.Frame(nb, padding=8)
        self.plan_panel = PlanPanel(nb, self)
        nb.add(self.server_tab, text="Servidor")
        self.kv_panel = KVPanel(nb, self)
        nb.add(self.kv_panel, text="KV de sesiones")
        nb.add(self.plan_panel, text="Plan y objetivo")
        nb.add(self.infer_tab, text="Inference")
        nb.add(self.bench_tab, text="Benchmark")
        nb.add(self.system_tab, text="System")
        self.chat_panel = ChatPanel(nb, self)
        nb.add(self.chat_panel, text="Chat")
        self.lab_panel = LabPanel(nb, self)
        nb.add(self.lab_panel, text="Experiments")
        self.monitor_panel = MonitorPanel(nb)
        nb.add(self.monitor_panel, text="Monitor")

        self._build_inference_tab()
        self._build_bench_tab()
        self._build_server_tab()
        self._build_system_tab()
        nb.select(self.server_tab)

        # Bottom log
        bottom = ttk.Frame(self.workbench_pane, padding=(0,0,0,4), height=190)
        bottom.pack_propagate(False)
        self.workbench_pane.add(bottom, weight=1)
        header = ttk.Frame(bottom)
        header.pack(fill="x")
        ttk.Label(header, text="OUTPUT", font=("Segoe UI", 10, "bold")).pack(side="left")
        ttk.Button(header, text="Clear", command=lambda: self.log.delete("1.0", "end")).pack(side="right")
        ttk.Button(header, text="Stop", command=self.stop_process).pack(side="right", padx=4)
        self.bottom_notebook = ttk.Notebook(bottom)
        self.bottom_notebook.pack(fill="both", expand=True)
        output = ttk.Frame(self.bottom_notebook)
        self.bottom_notebook.add(output, text="Salida y diagnóstico")
        self.log = self._scrolled(output, tk.Text, height=5, wrap="word")
        style_text(self.log)
        self.command_panel = CommandPanel(self.bottom_notebook, self)
        self.bottom_notebook.add(self.command_panel, text="Comandos · inspeccionar y reconstruir")

        status = ttk.Frame(self, padding=(8,0,8,5))
        status.grid(row=3, column=0, sticky="ew")
        ttk.Label(status, textvariable=self.status_var).pack(side="left")
        ttk.Button(status, text="Copiar comando", command=self.command_panel.copy_command).pack(side="right", padx=4)
        ttk.Button(status, text="Inspeccionar comando", command=lambda: self.bottom_notebook.select(self.command_panel)).pack(side="right")
        ttk.Entry(status, textvariable=self.command_var, state="readonly").pack(side="right", fill="x", expand=True, padx=12)
        def arrange():
            pane.sashpos(0, 350)
            library.sashpos(0, int(library.winfo_height() * 0.70))
            # Let the nested panes settle before reserving space for the console.
            self.after_idle(lambda: self.workbench_pane.sashpos(0, max(330, self.workbench_pane.winfo_height() - 190)))
        self.after(80, arrange)


    def _spin(self, parent, label, var, row, frm=0, to=999999, width=9):
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", pady=2)
        ttk.Spinbox(parent, textvariable=var, from_=frm, to=to, width=width, increment=0.05 if isinstance(var, tk.DoubleVar) else 1).grid(row=row, column=1, sticky="ew", pady=2)


    def _build_inference_tab(self):
        f = self.infer_tab
        f.columnconfigure(0, weight=1)
        f.rowconfigure(0, weight=1)
        work = ttk.Panedwindow(f, orient="vertical")
        work.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        self.prompt_editor = PromptEditor(work, self)
        work.add(self.prompt_editor, weight=2)
        response = ttk.Notebook(work)
        final = ttk.Frame(response)
        thought = ttk.Frame(response)
        response.add(final, text="Respuesta")
        response.add(thought, text="Razonamiento del modelo")
        self.inference_response = self._scrolled(final, tk.Text, height=6, wrap="word")
        self.inference_thoughts = self._scrolled(thought, tk.Text, height=6, wrap="word")
        for text in (self.inference_response, self.inference_thoughts):
            style_text(text)
            text.configure(state="disabled")
        work.add(response, weight=2)
        configuration = ScrolledForm(f)
        configuration.canvas.configure(width=285)
        configuration.grid(row=0, column=1, sticky="ns")
        parameters = configuration.content
        parameters.columnconfigure(1, weight=1)
        ttk.Label(parameters, text="PARÁMETROS", style="Title.TLabel").grid(row=0, column=0, columnspan=2, sticky="w", pady=6)
        for row, (label, var, low, high) in enumerate((
            ("Contexto", self.ctx_var, 256, 1048576), ("Capas GPU", self.ngl_var, 0, 999),
            ("Hilos CPU", self.threads_var, 1, 256), ("Batch", self.batch_var, 1, 8192),
            ("µBatch", self.ubatch_var, 1, 8192), ("Tokens salida", self.max_tokens_var, 1, 100000),
            ("Temperatura", self.temp_var, 0, 5), ("Top-p", self.top_p_var, 0, 1),
            ("Top-k", self.top_k_var, 0, 10000), ("Semilla", self.seed_var, -1, 2147483647)), 1):
            self._spin(parameters, label, var, row, low, high)

        ttk.Checkbutton(parameters, text="Flash Attention", variable=self.flash_var).grid(row=11, column=0, columnspan=2, sticky="w")
        ttk.Checkbutton(parameters, text="Memory map model", variable=self.mmap_var).grid(row=12, column=0, columnspan=2, sticky="w")

        btns = ttk.Frame(f)
        btns.grid(row=1, column=0, columnspan=2, sticky="ew", pady=8)
        ttk.Button(btns, text="Preview command", command=self.preview_inference).pack(side="left")
        self._action_button(btns, "RUN INFERENCE", self.run_inference).pack(side="left", padx=6)
        ttk.Label(btns, text="Respuesta arriba · diagnóstico completo en Salida", style="Muted.TLabel").pack(side="left", padx=8)


    def _build_bench_tab(self):
        f = self.bench_tab
        f.columnconfigure(1, weight=1)
        self._spin(f, "Prompt tokens (-p)", self.bench_prompt_var, 0, 1, 100000)
        self._spin(f, "Generation tokens (-n)", self.bench_gen_var, 1, 1, 100000)
        self._spin(f, "Repetitions (-r)", self.bench_reps_var, 2, 1, 100)
        self._spin(f, "GPU layers (-ngl)", self.ngl_var, 3, 0, 999)
        self._spin(f, "Threads (-t)", self.threads_var, 4, 1, 256)

        ttk.Label(
            f,
            text="Runs llama-bench against the selected model and stores the raw result under workspace\\benchmarks.",
            wraplength=500,
        ).grid(row=5, column=0, columnspan=2, sticky="w", pady=10)

        btns = ttk.Frame(f)
        btns.grid(row=6, column=0, columnspan=2, sticky="w")
        ttk.Button(btns, text="Preview", command=self.preview_bench).pack(side="left")
        self._action_button(btns, "RUN BENCHMARK", self.run_benchmark).pack(side="left", padx=6)
        ttk.Button(btns, text="Open results", command=self.open_benchmarks).pack(side="left")


    def _build_server_tab(self):
        self.server_panel = ServerPanel(self.server_tab, self)


    def _build_system_tab(self):
        f = self.system_tab
        ttk.Label(f, text="Carga. Mide. Compara. Ajusta. Verifica.", style="Title.TLabel").pack(anchor="w", pady=(0, 10))
        guards = ttk.Frame(f)
        guards.pack(fill="x", pady=8)
        self._spin(guards, "Thermal stop (°C; 0 disables)", self.thermal_limit_var, 0, 0, 110)
        self._spin(guards, "Inference/benchmark time limit (s)", self.job_timeout_var, 1, 5, 86400)
        ttk.Label(f, text="The thermal guard stops RuntimeDeck's own jobs when a sampled GPU reaches the limit. Telemetry is sampled every 2 seconds; missing sensors cannot trigger it.", wraplength=850, style="Muted.TLabel").pack(fill="x", pady=5)
        ttk.Button(f, text="Probe NVIDIA GPU", command=self.probe_gpu).pack(anchor="w")
        ttk.Button(f, text="Show selected paths", command=self.show_paths).pack(anchor="w", pady=5)
        ttk.Button(f, text="Open settings folder", command=self.open_settings_folder).pack(anchor="w")
        self.system_text = self._scrolled(f, tk.Text, height=18, wrap="word")
        style_text(self.system_text)
        ttk.Label(f, text='Porque «parece más rápido» no es una métrica.', style="Muted.TLabel").pack(anchor="w", pady=5)


    def _action_button(self, parent, label, command):
        button = ttk.Button(parent, text=label, command=command, style="Accent.TButton")
        self.action_buttons.append(button)
        return button

    def _scrolled(self, parent, factory, expand=True, **options):
        frame = ttk.Frame(parent)
        frame.pack(fill="both", expand=expand, pady=4)
        widget = factory(frame, **options)
        scrollbar = ttk.Scrollbar(frame, orient="vertical", command=widget.yview)
        scrollbar.pack(side="right", fill="y")
        widget.configure(yscrollcommand=scrollbar.set)
        if isinstance(widget, ttk.Treeview):
            horizontal = ttk.Scrollbar(frame, orient="horizontal", command=widget.xview)
            horizontal.pack(side="bottom", fill="x")
            widget.configure(xscrollcommand=horizontal.set)
        widget.pack(side="left", fill="both", expand=True)
        return widget
