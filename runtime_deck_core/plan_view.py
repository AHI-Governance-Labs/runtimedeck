"""An explicit objective, acceptance thresholds, stages and decision evidence."""

import json
import tkinter as tk
from tkinter import filedialog, ttk
from pathlib import Path

from .theme import style_text
from .widgets import ScrolledForm, BoundText


class PlanPanel(ttk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent, padding=8)
        self.app, self.controller = app, app.plan
        self.columnconfigure(0, weight=1)
        self.columnconfigure(1, weight=2)
        self.rowconfigure(1, weight=1)
        ttk.Label(self, text="OBJETIVO → PRUEBAS → EVIDENCIA → DECISIÓN", style="Title.TLabel").grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 8))
        form = ScrolledForm(self)
        form.canvas.configure(width=330, height=280)
        form.grid(row=1, column=0, sticky="nsew", padx=(0, 8))
        f = form.content
        f.columnconfigure(0, weight=1)
        c = self.controller
        ttk.Label(f, text="¿Qué problema quieres resolver?").grid(row=0, column=0, sticky="w")
        ttk.Entry(f, textvariable=c.title).grid(row=1, column=0, sticky="ew", pady=(4, 10))
        ttk.Label(f, text="Pregunta / uso previsto").grid(row=2, column=0, sticky="w")
        self.question_editor = BoundText(f, c.question, height=3, wrap="word", undo=True)
        style_text(self.question_editor)
        self.question_editor.grid(row=3, column=0, sticky="ew", pady=(4, 10))
        ttk.Label(f, text="CRITERIOS DE ACEPTACIÓN", style="Title.TLabel").grid(row=4, column=0, sticky="w", pady=8)
        labels = {"generation_tps": "Generación mínima sostenida (t/s)", "quality_pass_pct": "Aciertos mínimos del dataset (%)",
                  "temperature_c": "Temperatura máxima (°C; 0 ignora)", "memory_mib": "VRAM total máxima (MiB; 0 ignora)",
                  "stability_runs": "Ejecuciones de estabilidad"}
        row = 5
        for key, label in labels.items():
            ttk.Label(f, text=label).grid(row=row, column=0, sticky="w", pady=(6, 2))
            ttk.Entry(f, textvariable=c.criteria_vars[key], width=15).grid(row=row + 1, column=0, sticky="ew")
            row += 2
        ttk.Label(f, text="Dataset JSONL · la conclusión se limita a sus casos", wraplength=320).grid(row=row, column=0, sticky="w", pady=(10, 3))
        ttk.Entry(f, textvariable=app.evaluation.dataset).grid(row=row + 1, column=0, sticky="ew")
        ttk.Button(f, text="Elegir dataset", command=self.choose_dataset).grid(row=row + 2, column=0, sticky="w", pady=5)
        ttk.Checkbutton(f, text="Ajustar automáticamente antes de validar", variable=c.autotune).grid(row=row + 3, column=0, sticky="w")
        ttk.Checkbutton(f, text="Incluir escalado de prompts", variable=c.scaling).grid(row=row + 4, column=0, sticky="w")
        ttk.Label(f, text="Los candidatos y tamaños se toman de Experiments. El plan ejecuta las etapas en orden, conserva la evidencia y valida la configuración elegida.", wraplength=320, style="Muted.TLabel").grid(row=row + 5, column=0, sticky="w", pady=10)
        right = ttk.Frame(self)
        right.grid(row=1, column=1, sticky="nsew")
        ttk.Label(right, text="PLAN DE EJECUCIÓN", style="Title.TLabel").pack(anchor="w")
        self.stages = app._scrolled(right, ttk.Treeview, expand=False, columns=("state", "evidence"), show="tree headings", height=5, style="Plan.Treeview")
        self.stages.heading("#0", text="Etapa")
        self.stages.heading("state", text="Estado")
        self.stages.heading("evidence", text="Evidencia")
        self.stages.column("#0", width=170)
        self.stages.column("state", width=95)
        self.stages.column("evidence", width=100)
        self.stages.bind("<<TreeviewSelect>>", self.details)
        ttk.Label(right, text="DICTAMEN Y TRAZABILIDAD", style="Title.TLabel").pack(anchor="w", pady=(8, 0))
        self.report = app._scrolled(right, tk.Text, wrap="word", height=9)
        style_text(self.report)
        self.report.configure(state="disabled")
        actions = ttk.Frame(self)
        actions.grid(row=2, column=0, columnspan=2, sticky="ew", pady=8)
        ttk.Button(actions, text="EJECUTAR PLAN", style="Accent.TButton", command=c.start).pack(side="left")
        ttk.Button(actions, text="Detener plan", command=c.stop).pack(side="left", padx=5)
        ttk.Button(actions, text="Guardar borrador", command=c.save).pack(side="left")
        ttk.Button(actions, text="Abrir plan", command=c.load).pack(side="left", padx=5)
        ttk.Button(actions, text="Copiar dictamen", command=self.copy_report).pack(side="left")
        ttk.Button(actions, text="Ver dictamen", command=lambda: self.render(c.document)).pack(side="left", padx=5)
        ttk.Label(self, textvariable=c.status, style="Muted.TLabel", wraplength=900).grid(row=3, column=0, columnspan=2, sticky="ew")
        self.render(None)

    def choose_dataset(self):
        path = filedialog.askopenfilename(filetypes=[("Casos JSONL", "*.jsonl")])
        if path:
            self.app.evaluation.dataset.set(path)

    def render(self, document):
        self.stages.delete(*self.stages.get_children())
        labels = {"baseline": "Medir referencia", "tuning": "Buscar candidatos", "scaling": "Escalar carga",
                  "stability": "Validar estabilidad", "answers": "Evaluar respuestas"}
        stages = (document or {}).get("stages", [{"kind": kind, "status": "pending"} for kind in labels])
        names = {"pending": "Pendiente", "running": "En curso", "finished": "Completado", "failed": "Falló", "cancelled": "Cancelado"}
        for index, stage in enumerate(stages, 1):
            evidence = len((document or {}).get("results", {}).get(stage["kind"], []))
            self.stages.insert("", "end", iid=stage["kind"], text=f"{index} · {labels.get(stage['kind'], stage['kind'])}",
                               values=(names.get(stage["status"], stage["status"]), f"{evidence} artefacto{'s' if evidence != 1 else ''}"))
        if document:
            assessment = document["assessment"]
            lines = [f"{document['title']}\n{document['question']}", f"Estado: {document['status']} · Dictamen: {assessment['state']}",
                     f"Modelo: {document['model']['name']}\nRuntime: {document['runtime']['label']}"]
            for criterion in assessment["criteria"]:
                measured = criterion["measured"]
                lines.append(f"{criterion['criterion']}: {criterion['state']}\n  Objetivo: {criterion['target']} · Medido: {measured if measured is not None else 'sin evidencia'}")
            lines.append(assessment["scope"])
            lines.append(f"Plan y resultados: {self.controller.path}")
            text = "\n\n".join(lines)
        else:
            text = ("RuntimeDeck te ayuda a decidir qué configuración satisface tu carga de trabajo.\n\n"
                    "1. Formula una pregunta y fija umbrales.\n2. Mide una referencia y busca candidatos.\n"
                    "3. Valida el candidato con carga, estabilidad y respuestas.\n4. Decide usando los artefactos guardados.\n\n"
                    "Los datos ausentes quedan pendientes. Un proceso que termine correctamente puede producir respuestas incorrectas.\n\n"
                    "El dataset de ejemplo comprueba la integración. Sustitúyelo por casos de tu trabajo para obtener una conclusión útil.")
        self._text(text)

    def _text(self, text):
        self.report.configure(state="normal")
        self.report.delete("1.0", "end")
        self.report.insert("1.0", text)
        self.report.configure(state="disabled")

    def details(self, event=None):
        selection = self.stages.selection()
        if selection and self.controller.document:
            records = self.controller.document.get("results", {}).get(selection[0], [])
            self._text(json.dumps(records, indent=2, ensure_ascii=False) if records else "Esta etapa todavía no tiene evidencia. Los criterios siguen pendientes.")

    def copy_report(self):
        self.clipboard_clear()
        self.clipboard_append(self.report.get("1.0", "end-1c"))
