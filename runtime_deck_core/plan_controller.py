"""Coordinate a captured objective through baseline, tuning, stability and quality."""

import copy
import json
import tkinter as tk
import uuid
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox

from .commands import build_command
from .evaluation import load_cases
from .experiments import best_result, tuning_plan
from .plans import assess_plan, atomic_json, validate_criteria
from .settings import validate_settings


class PlanController:
    def __init__(self, app):
        self.app = app
        self.running = False
        self.dispatching = False
        self.stopping = False
        self.document = None
        self.path = None
        self.stage_index = 0
        self.stage_active = False
        self.title = tk.StringVar(app, "Evaluar mi configuración local")
        self.question = tk.StringVar(app, "¿Mi modelo cumple la calidad y la velocidad que necesito dentro de los límites de mi GPU?")
        self.criteria_vars = {key: tk.StringVar(app, value) for key, value in
                              {"generation_tps": "25", "quality_pass_pct": "90", "temperature_c": "85",
                               "memory_mib": "11264", "stability_runs": "5"}.items()}
        self.autotune = tk.BooleanVar(app, True)
        self.scaling = tk.BooleanVar(app, True)
        self.status = tk.StringVar(app, "Define el objetivo, los umbrales y un dataset representativo.")

    def _definition(self):
        self.app.plan_panel.question_editor.flush()
        if not self.title.get().strip() or not self.question.get().strip():
            raise ValueError("Escribe un título y la pregunta que quieres resolver")
        criteria = validate_criteria({key: variable.get() for key, variable in self.criteria_vars.items()})
        values = self.app.collect_settings()
        model, runtime = self.app.selected_model, self.app.selected_runtime
        build_command("benchmark", model, runtime, values)
        build_command("server", model, runtime, values)
        stages = ["baseline"] + (["tuning"] if self.autotune.get() else []) + (["scaling"] if self.scaling.get() else []) + ["stability", "answers"]
        return {"schema_version": 1, "id": uuid.uuid4().hex, "title": self.title.get().strip(),
                "question": self.question.get().strip(), "created": datetime.now().isoformat(), "status": "draft",
                "criteria": criteria, "model": asdict(model), "runtime": asdict(runtime),
                "initial_settings": values, "candidate_settings": values.copy(),
                "dataset": self.app.evaluation.dataset.get(), "stages": [{"kind": stage, "status": "pending"} for stage in stages],
                "search": {"layers": self.app.lab.layers.get(), "threads": self.app.lab.threads.get(),
                           "batches": self.app.lab.batches.get(), "limit": self.app.lab.limit.get(),
                           "prompts": self.app.lab.prompts.get(), "timeout": self.app.lab.timeout.get()},
                "results": {}, "telemetry": []}

    def _persist(self):
        self.document["assessment"] = assess_plan(self.document)
        atomic_json(self.path, self.document)
        self.app.plan_panel.render(self.document)

    def save(self):
        try:
            if not self.running:
                self.document = self._definition()
                self.path = Path(self.document["initial_settings"]["root"]) / "plans" / f"{self.document['id']}.json"
            self._persist()
            self.status.set(f"Plan guardado · {self.path.name}")
        except (OSError, ValueError, tk.TclError) as exc:
            messagebox.showerror("Plan", str(exc))

    def load(self):
        if self.running:
            return
        path = filedialog.askopenfilename(initialdir=Path(self.app.root_var.get()) / "plans", filetypes=[("Plan", "*.json")])
        if not path:
            return
        try:
            document = json.loads(Path(path).read_text(encoding="utf-8"))
            validate_criteria(document["criteria"])
            validate_settings(document["initial_settings"])
            if not isinstance(document.get("stages"), list) or not isinstance(document.get("results"), dict):
                raise ValueError("El archivo no contiene un plan válido")
            self.title.set(document["title"])
            self.question.set(document["question"])
            for key, variable in self.criteria_vars.items():
                variable.set(document["criteria"][key])
            self.autotune.set(any(stage["kind"] == "tuning" for stage in document["stages"]))
            self.scaling.set(any(stage["kind"] == "scaling" for stage in document["stages"]))
            self.app.evaluation.dataset.set(document["dataset"])
            self.document, self.path = document, Path(path)
            if document["status"] == "running":
                document["status"] = "interrupted"
                document["assessment"] = assess_plan(document)
            self.app.plan_panel.render(document)
            self.status.set("Plan cargado. Ejecutar crea una nueva ejecución con la selección actual.")
        except (OSError, ValueError, KeyError, TypeError) as exc:
            messagebox.showerror("Plan", str(exc))

    def start(self):
        if self.running or self.app.runner.running or self.app.lab.running or self.app.chat.running or self.app.evaluation.running:
            messagebox.showwarning("Plan", "Detén la tarea activa antes de ejecutar el plan")
            return
        try:
            document = self._definition()
            load_cases(document["dataset"])
            if self.autotune.get():
                search = document["search"]
                tuning_plan(document["initial_settings"], search["layers"], search["threads"], search["batches"], search["limit"])
            self.document = document
            self.path = Path(document["initial_settings"]["root"]) / "plans" / f"{document['id']}.json"
            self.model, self.runtime = self.app.selected_model, self.app.selected_runtime
            self.document["status"] = "running"
            self.document["candidate_settings"]["thermal_limit"] = int(document["criteria"]["temperature_c"])
            self._persist()
        except (OSError, ValueError, tk.TclError) as exc:
            messagebox.showerror("Plan", str(exc))
            return
        self.running = True
        self.stopping = False
        self.stage_index = 0
        self.stage_active = False
        self.app.after(50, self._advance)

    def _advance(self):
        if not self.running:
            return
        try:
            if self.stage_active:
                stage = self.document["stages"][self.stage_index]
                mode = stage["kind"]
                if self.app.lab.running or self.app.evaluation.running:
                    self.app.after(100, self._advance)
                    return
                if mode == "answers":
                    result = self.app.evaluation.last_result
                    if not result or not result["artifact"]:
                        raise RuntimeError((result or {}).get("error") or "La evaluación terminó sin evidencia")
                    record = json.loads(Path(result["artifact"]).with_suffix(".json").read_text(encoding="utf-8"))
                    records = [record]
                else:
                    records = copy.deepcopy(self.app.lab.records)
                self.document["results"][mode] = records
                successful = records and all(record["status"] == "success" and record["exit_code"] == 0
                                             and not record.get("parameter_verification", {}).get("mismatches") for record in records)
                stage["status"] = "finished" if successful else "failed"
                if mode == "tuning" and successful:
                    winner = best_result(records, self.app.lab.objective.get())
                    if winner is None:
                        raise RuntimeError("No hay candidato medido válido")
                    for key in ("ngl", "threads", "batch", "ubatch"):
                        self.document["candidate_settings"][key] = winner["settings"][key]
                self.stage_index += 1
                self.stage_active = False
                self._persist()
                if not successful:
                    raise RuntimeError(f"La etapa {mode} falló. Revisa sus artefactos")
            if self.stopping:
                self._finish("cancelled")
                return
            if self.stage_index >= len(self.document["stages"]):
                self._finish("finished")
                return
            stage = self.document["stages"][self.stage_index]
            self.dispatching = True
            self.app.selected_model, self.app.selected_runtime = self.model, self.runtime
            self.app.apply_configuration(self.document["candidate_settings"])
            search = self.document["search"]
            for key in ("layers", "threads", "batches", "limit", "prompts", "timeout"):
                getattr(self.app.lab, key).set(search[key])
            self.app.lab.stability_runs.set(self.document["criteria"]["stability_runs"])
            self.app.evaluation.dataset.set(self.document["dataset"])
            mode = stage["kind"]
            if mode == "answers":
                self.app.evaluation.start()
                started = self.app.evaluation.running
            else:
                self.app.lab.start(mode)
                started = self.app.lab.running
            if not started:
                raise RuntimeError(f"No se pudo iniciar la etapa {mode}")
            stage["status"] = "running"
            self.stage_active = True
            self.status.set(f"{self.stage_index + 1}/{len(self.document['stages'])} · {mode} · selección y parámetros capturados")
            self._persist()
            self.app.after(100, self._advance)
        except (OSError, ValueError, RuntimeError, tk.TclError) as exc:
            self.document["error"] = str(exc)
            self._finish("cancelled" if self.stopping else "failed")
        finally:
            self.dispatching = False

    def sample(self, sample):
        if self.running and len(self.document["telemetry"]) < 5000:
            self.document["telemetry"].append(sample)

    def _finish(self, status):
        self.running = False
        self.document["status"] = status
        self.document["finished"] = datetime.now().isoformat()
        if status != "finished":
            self.app.lab.stop()
            self.app.evaluation.stop()
        if self.app.active_kind == "server":
            self.app.runner.stop()
        try:
            self._persist()
        except OSError as exc:
            self.status.set(f"Error al guardar el plan: {exc}")
            return
        self.status.set(f"{status} · dictamen: {self.document['assessment']['state']} · {self.document.get('error', self.path.name)}")

    def stop(self):
        if self.running:
            self.stopping = True
            self.app.lab.stop()
            self.app.evaluation.stop()
            self.app.runner.stop()
            self.status.set("Deteniendo plan; las evidencias parciales se conservarán")
