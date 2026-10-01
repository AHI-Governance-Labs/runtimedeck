"""Sequential experiment coordinator; workers never access Tk widgets."""

import json
import threading
import tkinter as tk
from dataclasses import asdict
from pathlib import Path
from tkinter import filedialog, messagebox

from .benchmarks import BenchmarkSession
from .commands import build_command, format_command
from .experiments import Trial, best_result, scaling_plan, stability_plan, tuning_plan
from .history import export_csv, load_history
from .metrics import explain_failure
from .processes import ProcessRunner
from .datasets import freeze_corpus


class LabController:
    def __init__(self, app):
        self.app = app
        self.running = False
        self.cancelled = False
        self.timed_out = False
        self.records = []
        self.history = []
        self.plan = []
        self.index = 0
        self.session = None
        self.timeout_handle = None
        self.model = None
        self.runtime = None
        self.mode = ""
        self.dataset_metadata = {}
        self.layers = tk.StringVar(app, "99")
        self.threads = tk.StringVar(app, "4,8")
        self.batches = tk.StringVar(app, "256,512,1024")
        self.prompts = tk.StringVar(app, "128,512,1024,2048")
        self.stability_runs = tk.IntVar(app, 5)
        self.limit = tk.IntVar(app, 16)
        self.timeout = tk.IntVar(app, 180)
        self.objective = tk.StringVar(app, "generation_tps")
        self.progress = tk.StringVar(app, "Select a model and runtime, then choose an experiment.")
        self.dataset = tk.StringVar(app, "")
        self.chunks = tk.IntVar(app, 2)
        self.runner = ProcessRunner(lambda text: app.msgq.put(("lab_log", text)),
                                    lambda code: app.msgq.put(("lab_done", code)))

    def start(self, mode):
        if self.running or self.app.active_kind is not None or self.app.runner.running or (self.app.plan.running and not self.app.plan.dispatching):
            messagebox.showwarning("RuntimeDeck", "Stop the active job before starting another.")
            return
        try:
            values = self.app.collect_settings()
            self.app.save_settings()
            self.model = self.app.selected_model
            self.runtime = self.app.selected_runtime
            if mode == "baseline":
                plan = [Trial("Referencia", values)]
            elif mode == "tuning":
                plan = tuning_plan(values, self.layers.get(), self.threads.get(), self.batches.get(), self.limit.get())
            elif mode == "scaling":
                plan = scaling_plan(values, self.prompts.get())
            elif mode == "stability":
                plan = stability_plan(values, self.stability_runs.get())
            else:
                dataset = Path(self.dataset.get())
                if not self.dataset.get() or not dataset.is_file():
                    raise ValueError("Choose a UTF-8 text corpus for perplexity evaluation.")
                if not 1 <= self.chunks.get() <= 1000:
                    raise ValueError("Chunks must be between 1 and 1000.")
                plan = [Trial("Perplexity", dict(values, quality_chunks=self.chunks.get()), "quality", str(dataset))]
            if not 5 <= self.timeout.get() <= 86400:
                raise ValueError("Trial timeout must be between 5 and 86400 seconds.")
            for trial in plan:
                build_command(trial.kind, self.model, self.runtime, trial.values, dataset=trial.dataset)
        except (ValueError, OSError, tk.TclError) as exc:
            messagebox.showerror("RuntimeDeck", str(exc))
            return
        self.mode = mode
        self.plan = plan
        self.index = 0
        self.records = []
        self.cancelled = False
        self.running = True
        self.dataset_metadata = {}
        for button in self.app.action_buttons:
            button.state(["disabled"])
        if mode == "quality":
            self.progress.set("Freezing and hashing the evaluation corpus...")
            def prepare():
                try:
                    source = freeze_corpus(plan[0].dataset, values["root"], lambda: self.cancelled)
                    self.app.msgq.put(("lab_corpus", (source, None)))
                except OSError as exc:
                    self.app.msgq.put(("lab_corpus", (None, str(exc))))
            threading.Thread(target=prepare, daemon=True).start()
        else:
            self._next()

    def _next(self):
        if self.cancelled or self.index >= len(self.plan):
            self._finish()
            return
        trial = self.plan[self.index]
        self.timed_out = False
        try:
            command = build_command(trial.kind, self.model, self.runtime, trial.values, dataset=trial.dataset)
        except (OSError, ValueError) as exc:
            # Files can disappear after planning. Restore controls rather than
            # leaving a Tk callback failure with the suite marked as running.
            self.app.log.insert("end", f"[experiment aborted] {exc}\n")
            self.cancelled = True
            self._finish()
            self.progress.set(f"Aborted · {exc}")
            self.app.status_var.set(self.progress.get())
            return
        self.session = BenchmarkSession(
            Path(trial.values["root"]), asdict(self.model), asdict(self.runtime),
            command,
            {"prompt_tokens": trial.values["bench_prompt"], "generation_tokens": trial.values["bench_gen"],
             "repetitions": trial.values["bench_reps"], "gpu_layers": trial.values["ngl"], "threads": trial.values["threads"]},
            settings=trial.values.copy(), experiment=self.mode, label=trial.label,
            dataset=self.dataset_metadata.copy(),
            plan_id=self.app.plan.document["id"] if self.app.plan.running else "",
        )
        self.progress.set(f"{self.index + 1}/{len(self.plan)} · {trial.label}")
        self.app.status_var.set(self.progress.get())
        self.app.log.insert("end", f"\n=== {self.mode}: {trial.label} ===\n")
        self.app.command_var.set(format_command(self.session.command))
        self._command_snapshot = self.app.command_panel.record(self.session.command, trial.kind, asdict(self.model),
                                                              asdict(self.runtime), trial.values, self.mode)
        try:
            self.runner.start(self.session.command, cwd=self.runtime.directory)
            self.timeout_handle = self.app.after(self.timeout.get() * 1000, self._timeout)
        except (OSError, RuntimeError) as exc:
            self.session.output.append(str(exc))
            self.app.msgq.put(("lab_done", -1))

    def _timeout(self):
        self.timeout_handle = None
        self.timed_out = True
        self.runner.stop()

    def event(self, kind, payload):
        if kind == "lab_corpus":
            source, error = payload
            if error or self.cancelled:
                self.app.log.insert("end", f"[corpus] {error or 'Cancelled'}\n")
                self.cancelled = True
                self._finish()
            else:
                self.dataset_metadata = source
                self.plan[0].dataset = source["path"]
                self._next()
        elif kind == "lab_history":
            self.history, warnings = payload
            self.app.lab_panel.render_history(self.history)
            for warning in warnings:
                self.app.log.insert("end", f"[history] {warning}\n")
        elif kind == "lab_log" and self.session is not None:
            self.session.output.append(str(payload))
            self.app.log.insert("end", str(payload))
        elif kind == "lab_done" and self.session is not None:
            if self.timeout_handle is not None:
                self.app.after_cancel(self.timeout_handle)
                self.timeout_handle = None
            code = int(payload)
            self.app.command_panel.finish(self._command_snapshot, code)
            self.session.status = self.session.status or ("timeout" if self.timed_out else "cancelled" if self.cancelled else "success" if code == 0 else "failed")
            try:
                path = self.session.save(code)
                record = json.loads(path.with_suffix(".json").read_text(encoding="utf-8"))
                record["artifact"] = str(path.with_suffix(".json"))
                self.records.append(record)
                verification = record.get("parameter_verification", {})
                if verification.get("state") == "mismatch":
                    self.app.log.insert("end", f"[parameter mismatch] {verification['mismatches']}\n")
                self.history.insert(0, record)
                self.app.lab_panel.render_history(self.history)
                self.app.log.insert("end", f"[saved] {path}\n")
            except (OSError, ValueError) as exc:
                self.app.log.insert("end", f"[result save failed] {exc}\n")
            if code:
                self.app.log.insert("end", f"[diagnosis] {explain_failure(''.join(self.session.output), code)}\n")
            self.session = None
            self.index += 1
            self.app.after(100, self._next)

    def _finish(self):
        self.running = False
        successful = sum(record.get("status") == "success" for record in self.records)
        winner = best_result(self.records, self.objective.get()) if self.mode == "tuning" else None
        self.progress.set(f"{'Cancelled' if self.cancelled else 'Finished'} · {successful}/{len(self.records)} successful trials")
        if winner:
            self.progress.set(self.progress.get() + f" · best: {winner['metrics'][self.objective.get()]:.2f} t/s")
        self.app.status_var.set(self.progress.get())
        for button in self.app.action_buttons:
            button.state(["!disabled"])

    def stop(self):
        if self.running:
            self.cancelled = True
            self.progress.set("Stopping experiment...")
            self.runner.stop()

    def apply_best(self):
        if self.running or self.app.active_kind is not None:
            messagebox.showwarning("RuntimeDeck", "Wait for the active job to finish.")
            return
        winner = best_result(self.records, self.objective.get()) if self.mode == "tuning" else None
        if winner is None:
            messagebox.showinfo("RuntimeDeck", "Run Auto tune first; no measured recommendation is available.")
            return
        if self.app.selected_model != self.model or self.app.selected_runtime != self.runtime:
            messagebox.showwarning("RuntimeDeck", "The recommendation belongs to a different model/runtime. Restore that selection first.")
            return
        for key in ("ngl", "threads", "batch", "ubatch"):
            getattr(self.app, f"{key}_var").set(winner["settings"][key])
        self.app.save_settings()
        self.progress.set("Measured recommendation applied to inference and benchmark controls.")

    def refresh_history(self):
        root = self.app.root_var.get()
        threading.Thread(target=lambda: self.app.msgq.put(("lab_history", load_history(root))), daemon=True).start()

    def export(self):
        path = filedialog.asksaveasfilename(defaultextension=".csv", filetypes=[("CSV", "*.csv")])
        if path:
            try:
                export_csv(self.history, path)
            except OSError as exc:
                messagebox.showerror("RuntimeDeck", str(exc))
