"""Experiment setup, measured history, comparison charts and quality evaluation."""

import json
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, ttk

from .theme import PALETTE, style_text
from .widgets import ScrolledForm
from .comparison import comparable_throughput


class LabPanel(ttk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent, padding=8)
        self.app = app
        self.controller = app.lab
        self.records = []
        notebook = ttk.Notebook(self)
        self.notebook = notebook
        notebook.pack(fill="both", expand=True)
        setup = ScrolledForm(notebook)
        results = ttk.Frame(notebook, padding=8)
        self.results_view = results
        quality = ScrolledForm(notebook)
        notebook.add(setup, text="Auto tune & suites")
        notebook.add(results, text="Results & comparison")
        notebook.add(quality, text="Quality")
        self._build_setup(setup.content)
        self._results(results)
        self._quality(quality.content)
        ttk.Label(self, textvariable=self.controller.progress, wraplength=850, style="Muted.TLabel").pack(fill="x", pady=8)

    def _entry(self, parent, row, label, variable):
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", padx=(0, 12), pady=5)
        ttk.Entry(parent, textvariable=variable).grid(row=row, column=1, sticky="ew", pady=5)

    def _build_setup(self, frame):
        lab = self.controller
        frame.columnconfigure(1, weight=1)
        ttk.Label(frame, text="MEASURE · COMPARE · OPTIMIZE", style="Title.TLabel").grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 10))
        ttk.Label(frame, text="Comma-separated candidates. All trials use the selected model, runtime and benchmark workload.", wraplength=700).grid(row=1, column=0, columnspan=2, sticky="w", pady=8)
        self._entry(frame, 2, "GPU layer candidates", lab.layers)
        self._entry(frame, 3, "CPU thread candidates", lab.threads)
        self._entry(frame, 4, "Batch candidates", lab.batches)
        self._entry(frame, 5, "Maximum grid size", lab.limit)
        self._entry(frame, 6, "Timeout per trial (seconds)", lab.timeout)
        ttk.Label(frame, text="Optimize for").grid(row=7, column=0, sticky="w", pady=5)
        ttk.Combobox(frame, textvariable=lab.objective, state="readonly",
                     values=("generation_tps", "prompt_tps")).grid(row=7, column=1, sticky="ew")
        actions = ttk.Frame(frame)
        actions.grid(row=8, column=0, columnspan=2, sticky="w", pady=12)
        self.app._action_button(actions, "AUTO TUNE", lambda: lab.start("tuning")).pack(side="left")
        ttk.Button(actions, text="Apply measured best", command=lab.apply_best).pack(side="left", padx=8)
        ttk.Separator(frame).grid(row=9, column=0, columnspan=2, sticky="ew", pady=10)
        self._entry(frame, 10, "Prompt scaling (tokens)", lab.prompts)
        self._entry(frame, 11, "Stability runs", lab.stability_runs)
        actions = ttk.Frame(frame)
        actions.grid(row=12, column=0, columnspan=2, sticky="w", pady=10)
        self.app._action_button(actions, "PROMPT SCALING", lambda: lab.start("scaling")).pack(side="left")
        self.app._action_button(actions, "STABILITY TEST", lambda: lab.start("stability")).pack(side="left", padx=8)

    def _results(self, frame):
        controls = ttk.Frame(frame)
        controls.pack(fill="x")
        ttk.Button(controls, text="Refresh saved results", command=self.controller.refresh_history).pack(side="left")
        ttk.Button(controls, text="Export CSV", command=self.controller.export).pack(side="left", padx=8)
        self.summary = tk.StringVar(self, "No results loaded")
        ttk.Label(controls, textvariable=self.summary, style="Muted.TLabel").pack(side="right")
        columns = ("time", "model", "status", "prompt", "gen", "ppl", "quality", "threads", "layers")
        self.table = self.app._scrolled(frame, ttk.Treeview, columns=columns, show="headings", height=7, selectmode="browse")
        for key, label, width in zip(columns,
            ("Started", "Model", "Status", "Prompt t/s", "Gen t/s", "PPL", "Cases %", "Threads", "GPU layers"),
            (135, 200, 85, 90, 90, 70, 75, 65, 75)):
            self.table.heading(key, text=label)
            self.table.column(key, width=width, minwidth=50, stretch=key == "model")
        self.table.bind("<<TreeviewSelect>>", self._details)
        self.chart = tk.Canvas(frame, height=145, background=PALETTE["field"], highlightthickness=0)
        self.chart.pack(fill="x", pady=6)
        self.chart.bind("<Configure>", lambda event: self._chart())
        self.details = self.app._scrolled(frame, tk.Text, height=8, wrap="word")
        style_text(self.details)

    def _quality(self, frame):
        lab = self.controller
        frame.columnconfigure(1, weight=1)
        ttk.Label(frame, text="CORPUS PERPLEXITY", style="Title.TLabel").grid(row=0, column=0, columnspan=2, sticky="w", pady=10)
        ttk.Label(frame, text="Evaluate llama-perplexity on your own UTF-8 corpus. Compare the same corpus, tokenizer and context. Lower PPL is better on that corpus; it does not establish overall model quality.", wraplength=700).grid(row=1, column=0, columnspan=2, sticky="w", pady=12)
        self._entry(frame, 2, "Text corpus", lab.dataset)
        ttk.Button(frame, text="Browse corpus", command=self._choose_corpus).grid(row=3, column=1, sticky="w")
        self._entry(frame, 4, "Maximum chunks", lab.chunks)
        ttk.Label(frame, text="Uses context, GPU layers, threads and batch controls from Inference. The corpus must contain enough tokens for the selected context/chunks.", wraplength=700).grid(row=5, column=0, columnspan=2, sticky="w", pady=15)
        self.app._action_button(frame, "EVALUATE QUALITY", lambda: lab.start("quality")).grid(row=6, column=0, columnspan=2, sticky="w")
        ttk.Separator(frame).grid(row=7, column=0, columnspan=2, sticky="ew", pady=15)
        ttk.Label(frame, text="ANSWER REGRESSION DATASET", style="Title.TLabel").grid(row=8, column=0, columnspan=2, sticky="w")
        ttk.Label(frame, text="JSONL cases: prompt, check (exact / contains / regex / json), expected. Uses the local Chat engine. Example cases test the pipeline; create a representative dataset to assess quality.", wraplength=700).grid(row=9, column=0, columnspan=2, sticky="w", pady=10)
        self._entry(frame, 10, "JSONL cases", self.app.evaluation.dataset)
        ttk.Button(frame, text="Browse cases", command=self._choose_cases).grid(row=11, column=1, sticky="w")
        ttk.Button(frame, text="EVALUATE ANSWERS", style="Accent.TButton", command=self.app.evaluation.start).grid(row=12, column=0, columnspan=2, sticky="w", pady=10)
        ttk.Label(frame, textvariable=self.app.evaluation.status, wraplength=700, style="Muted.TLabel").grid(row=13, column=0, columnspan=2, sticky="w")

    def _choose_cases(self):
        path = filedialog.askopenfilename(filetypes=[("JSONL cases", "*.jsonl"), ("All files", "*.*")])
        if path:
            self.app.evaluation.dataset.set(path)

    def _choose_corpus(self):
        path = filedialog.askopenfilename(filetypes=[("Text corpus", "*.txt"), ("All files", "*.*")])
        if path:
            self.controller.dataset.set(path)

    def render_history(self, records):
        self.records = records
        self.table.delete(*self.table.get_children())
        for index, record in enumerate(records):
            metrics, parameters = record.get("metrics", {}), record.get("parameters", {})
            values = [str(record.get("timestamp", "")).replace("T", " ")[:19],
                      (record.get("model") or {}).get("name", ""), record.get("status", "")]
            values += [f"{metrics[key]:.2f}" if isinstance(metrics.get(key), (float, int)) else "—"
                       for key in ("prompt_tps", "generation_tps", "perplexity", "quality_pass_pct")]
            values += [parameters.get("threads", ""), parameters.get("gpu_layers", "")]
            self.table.insert("", "end", iid=f"h{index}", values=values)
        successes = sum(record.get("status") == "success" for record in records)
        self.summary.set(f"{len(records)} records · {successes} successful")
        self._chart()

    def _chart(self):
        self.chart.delete("all")
        selection = self.table.selection()
        reference = self.records[int(selection[0][1:])] if selection else None
        measured = comparable_throughput(self.records, reference)[:6]
        self.chart.create_text(12, 12, anchor="nw", text="Generation t/s · same recorded model and token workload · select a row to compare", fill=PALETTE["muted"])
        if not measured:
            return
        maximum = max(record["metrics"]["generation_tps"] for record in measured) or 1
        width = max(200, self.chart.winfo_width()) - 215
        for index, record in enumerate(measured):
            score = record["metrics"]["generation_tps"]
            y = 38 + index * 17
            label = record.get("label") or (record.get("runtime") or {}).get("label", "run")
            self.chart.create_text(12, y + 5, anchor="w", text=label[:24], fill=PALETTE["text"])
            self.chart.create_rectangle(180, y, 180 + width * score / maximum, y + 11, fill=PALETTE["accent"], outline="")
            self.chart.create_text(185 + width * score / maximum, y + 5, anchor="w", text=f"{score:.1f}", fill=PALETTE["text"])

    def _details(self, event=None):
        selection = self.table.selection()
        if not selection:
            return
        record = self.records[int(selection[0][1:])]
        self._chart()
        self.details.delete("1.0", "end")
        self.details.insert("end", json.dumps(record, indent=2, ensure_ascii=False))
        path = Path(record.get("raw_output_file", ""))
        try:
            self.details.insert("end", "\n\nRAW OUTPUT\n" + path.read_text(encoding="utf-8", errors="replace")[-30000:])
        except OSError as exc:
            self.details.insert("end", f"\nRaw output unavailable: {exc}")
