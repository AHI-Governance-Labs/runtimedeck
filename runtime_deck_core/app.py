"""Application controller: Tk state, event dispatch, and user actions."""
from __future__ import annotations

import os
import queue
import socket
import threading
import time
import tkinter as tk
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox
from typing import Optional

from .benchmarks import BenchmarkSession
from .commands import build_command, format_command
from .config import APP_NAME, DEFAULT_ROOT, SETTINGS_PATH
from .discovery import discover_workspace
from .models import ModelItem, RuntimeItem, human_size
from .processes import ProcessRunner
from .settings import DEFAULTS, SettingsStore, validate_value
from .system import open_folder, probe_nvidia_gpu
from .view import RuntimeDeckView
from .lab import LabController
from .chat import ChatController
from .telemetry import GpuMonitor
from .evaluation import EvaluationController
from .safety import thermal_violation
from .catalog import model_label
from .plan_controller import PlanController
from .settings import validate_settings
from .inference_output import split_inference_output


class RuntimeDeck(RuntimeDeckView):
    def __init__(self):
        super().__init__()
        self.title("RuntimeDeck — Local Inference Workbench")
        self.geometry("1500x900")
        self.minsize(1180, 720)

        self.models: list[ModelItem] = []
        self.runtimes: list[RuntimeItem] = []
        self.selected_model: Optional[ModelItem] = None
        self.selected_runtime: Optional[RuntimeItem] = None

        self.msgq: "queue.Queue[tuple[str, object]]" = queue.Queue()
        self.runner = ProcessRunner(
            lambda s: self.msgq.put(("log", s)),
            lambda c: self.msgq.put(("done", c)),
        )

        self.settings_store = SettingsStore()
        self.action_buttons = []
        self.active_kind = None
        self.benchmark = None
        self._scan_generation = 0
        self._gpu_pending = False
        self._closing = False
        self.settings = self.load_settings()
        root = self.settings.get("root", str(DEFAULT_ROOT))
        self.root_var = tk.StringVar(value=root)
        self.status_var = tk.StringVar(value="Ready")
        self.command_var = tk.StringVar(value="")
        self.gpu_var = tk.StringVar(value="GPU: probing...")
        self.model_info_var = tk.StringVar(value="No model selected")
        self.runtime_info_var = tk.StringVar(value="No runtime selected")
        self.model_filter_var = tk.StringVar(self, "")
        self._inference_raw = []
        self._active_command_snapshot = None

        self.ctx_var = tk.IntVar(value=int(self.settings.get("ctx", 8192)))
        self.ngl_var = tk.IntVar(value=int(self.settings.get("ngl", 99)))
        self.threads_var = tk.IntVar(value=int(self.settings.get("threads", max(1, (os.cpu_count() or 8)//2))))
        self.batch_var = tk.IntVar(value=int(self.settings.get("batch", 512)))
        self.ubatch_var = tk.IntVar(value=int(self.settings.get("ubatch", 256)))
        self.max_tokens_var = tk.IntVar(value=int(self.settings.get("max_tokens", 256)))
        self.temp_var = tk.DoubleVar(value=float(self.settings.get("temp", 0.7)))
        self.top_p_var = tk.DoubleVar(value=float(self.settings.get("top_p", 0.95)))
        self.top_k_var = tk.IntVar(value=int(self.settings.get("top_k", 40)))
        self.seed_var = tk.IntVar(value=int(self.settings.get("seed", -1)))
        self.flash_var = tk.BooleanVar(value=bool(self.settings.get("flash", True)))
        self.mmap_var = tk.BooleanVar(value=bool(self.settings.get("mmap", True)))
        self.prompt_var = tk.StringVar(value=self.settings.get("prompt", "Explica brevemente qué runtime estás usando."))
        self.server_port_var = tk.IntVar(value=int(self.settings.get("server_port", 8080)))
        self.server_host_var = tk.StringVar(value=self.settings.get("server_host", "127.0.0.1"))
        self.bench_prompt_var = tk.IntVar(value=int(self.settings.get("bench_prompt", 512)))
        self.bench_gen_var = tk.IntVar(value=int(self.settings.get("bench_gen", 128)))
        self.bench_reps_var = tk.IntVar(value=int(self.settings.get("bench_reps", 3)))
        self.thermal_limit_var = tk.IntVar(value=self.settings["thermal_limit"])
        self.job_timeout_var = tk.IntVar(value=self.settings["job_timeout"])
        self._job_timer = None
        self._job_stop_reason = ""

        self.lab = LabController(self)
        self.chat = ChatController(self)
        self.evaluation = EvaluationController(self)
        self.plan = PlanController(self)
        self.server_values = None
        self.server_model = None
        self.server_runtime = None
        self.monitor = GpuMonitor(lambda sample: self.msgq.put(("telemetry", sample)))
        self._build_ui()
        self.model_filter_var.trace_add("write", lambda *args: self._render_catalog())
        self.after(50, self._drain_queue)
        self.after(150, self.scan)
        self.after(300, self.probe_gpu)
        self.after(400, self.lab.refresh_history)
        self.after(500, self.monitor.start)
        self.protocol("WM_DELETE_WINDOW", self.on_close)


    def load_settings(self):
        return self.settings_store.load()


    def save_settings(self):
        self.settings_store.save(self.collect_settings())

    def collect_settings(self):
        if hasattr(self, "prompt_editor"):
            self.prompt_editor._changed()
        values = {}
        for key, default in DEFAULTS.items():
            try:
                variable = getattr(self, f"{key}_var")
                # IntVar.get() truncates fractional input; validate the original value.
                raw = variable.get() if isinstance(default, (bool, str)) else self.getvar(variable._name)
                values[key] = validate_value(key, raw)
            except tk.TclError as exc:
                raise ValueError(f"{key}: enter a valid number.") from exc
        return values

    def is_busy(self):
        return self.runner.running or self.lab.running or self.chat.running or self.evaluation.running or self.plan.running

    def apply_configuration(self, values):
        values = validate_settings(values)
        for key, value in values.items():
            getattr(self, f"{key}_var").set(value)

    def select_inventory(self, model, runtime):
        self.model_filter_var.set("")
        self.model_tree.selection_set(f"m{self.models.index(model)}")
        self.runtime_tree.selection_set(f"r{self.runtimes.index(runtime)}")
        self.on_model_select()
        self.on_runtime_select()

    def _render_catalog(self):
        previous = self.selected_model.path if self.selected_model else None
        self.model_tree.delete(*self.model_tree.get_children())
        query = self.model_filter_var.get().strip().casefold()
        groups = {}
        for index, model in enumerate(self.models):
            label = model_label(model, self.root_var.get())
            if query and query not in " ".join([model.name, *label.values()]).casefold():
                continue
            group = label["family"] if label["role"] == "Modelos" else label["role"]
            if group not in groups:
                iid = f"family{len(groups)}"
                groups[group] = iid
                self.model_tree.insert("", "end", iid=iid, text=group, open=group != "Auxiliares")
            self.model_tree.insert(groups[group], "end", iid=f"m{index}", text=label["title"],
                                   values=(label["quantization"], human_size(model.size)))
            if model.path == previous:
                self.model_tree.selection_set(f"m{index}")


    def choose_root(self):
        p = filedialog.askdirectory(initialdir=self.root_var.get() or str(DEFAULT_ROOT))
        if p:
            self.root_var.set(p)
            self.scan()


    def open_root(self):
        self._open_folder(Path(self.root_var.get()))

    def open_settings_folder(self):
        self._open_folder(SETTINGS_PATH.parent)

    def _open_folder(self, path):
        try:
            open_folder(path)
        except OSError as exc:
            messagebox.showerror(APP_NAME, str(exc))


    def open_benchmarks(self):
        self._open_folder(Path(self.root_var.get()) / "benchmarks")


    def scan(self):
        if self.plan.running:
            self.status_var.set("El inventario permanece capturado mientras se ejecuta el plan")
            return
        workspace = Path(self.root_var.get()).expanduser()
        self._scan_generation += 1
        generation = self._scan_generation
        self.status_var.set("Scanning workspace...")

        def worker():
            try:
                result = discover_workspace(workspace)
                self.msgq.put(("scan", (generation, result, None)))
            except (OSError, ValueError) as exc:
                self.msgq.put(("scan", (generation, None, str(exc))))

        threading.Thread(target=worker, daemon=True).start()

    def _apply_scan(self, payload):
        if self.plan.running:
            return
        generation, result, error = payload
        if generation != self._scan_generation:
            return
        if error:
            self.status_var.set("Workspace scan failed")
            messagebox.showerror(APP_NAME, error)
            return
        model_path = self.selected_model.path if self.selected_model else None
        runtime_path = self.selected_runtime.directory if self.selected_runtime else None
        self.models, self.runtimes, warnings = result
        self.selected_model = None
        self.selected_runtime = None
        self.model_info_var.set("No model selected")
        self.runtime_info_var.set("No runtime selected")
        self.model_tree.delete(*self.model_tree.get_children())
        self.runtime_tree.delete(*self.runtime_tree.get_children())
        self._render_catalog()
        for index, runtime in enumerate(self.runtimes):
            self.runtime_tree.insert("", "end", iid=f"r{index}", text=runtime.label, values=(runtime.capabilities,))
        for prefix, items, widget, previous, attribute, callback in (
            ("m", self.models, self.model_tree, model_path, "path", self.on_model_select),
            ("r", self.runtimes, self.runtime_tree, runtime_path, "directory", self.on_runtime_select),
        ):
            if items:
                index = next((i for i, item in enumerate(items) if getattr(item, attribute) == previous), 0)
                if prefix == "m" and not widget.exists(f"m{index}"):
                    self.model_filter_var.set("")
                widget.selection_set(f"{prefix}{index}")
                widget.focus(f"{prefix}{index}")
                callback()
        for warning in warnings:
            self.log.insert("end", f"[scan] {warning}\n")
        if self.active_kind is None:
            self.status_var.set(f"{len(self.models)} model file(s), {len(self.runtimes)} runtime(s)")


    def on_model_select(self, event=None):
        sel = self.model_tree.selection()
        if not sel:
            return
        if not sel[0].startswith("m") or not sel[0][1:].isdigit():
            return
        idx = int(sel[0][1:])
        if self.plan.running and self.models[idx] != self.plan.model:
            self.model_tree.selection_set(f"m{self.models.index(self.plan.model)}")
            self.status_var.set("El plan conserva el modelo capturado")
            return
        self.selected_model = self.models[idx]
        m = self.selected_model
        self.model_info_var.set(f"{m.path}\n{human_size(m.size)}")


    def on_runtime_select(self, event=None):
        sel = self.runtime_tree.selection()
        if not sel:
            return
        idx = int(sel[0][1:])
        if self.plan.running and self.runtimes[idx] != self.plan.runtime:
            self.runtime_tree.selection_set(f"r{self.runtimes.index(self.plan.runtime)}")
            return
        self.selected_runtime = self.runtimes[idx]
        r = self.selected_runtime
        self.runtime_info_var.set(f"{r.directory}\n{r.capabilities}")


    def build_inference(self):
        return build_command("inference", self.selected_model, self.selected_runtime, self.collect_settings())


    def build_bench(self):
        return build_command("benchmark", self.selected_model, self.selected_runtime, self.collect_settings())


    def build_server(self):
        return build_command("server", self.selected_model, self.selected_runtime, self.collect_settings())


    def _preview(self, builder):
        try:
            argv = builder()
            s = format_command(argv)
            self.command_var.set(s)
            kind = {"build_inference": "inference", "build_bench": "benchmark", "build_server": "server"}[builder.__name__]
            self.command_panel.record(argv, kind, asdict(self.selected_model), asdict(self.selected_runtime), self.collect_settings())
            self.bottom_notebook.select(self.command_panel)
            self.log.insert("end", "\n$ " + s + "\n")
            self.log.see("end")
        except Exception as e:
            messagebox.showerror(APP_NAME, str(e))


    def preview_inference(self):
        self._preview(self.build_inference)


    def preview_bench(self):
        self._preview(self.build_bench)


    def preview_server(self):
        self._preview(self.build_server)


    def _run(self, argv, kind, benchmark=None):
        if self.active_kind is not None or self.runner.running or self.lab.running or (self.plan.running and not self.plan.dispatching):
            messagebox.showwarning(APP_NAME, "A process is already running.")
            return False
        self.save_settings()
        if kind == "server":
            values = self.collect_settings()
            family = socket.AF_INET6 if ":" in values["server_host"] else socket.AF_INET
            try:
                with socket.socket(family, socket.SOCK_STREAM) as probe:
                    probe.bind((values["server_host"], values["server_port"]))
            except OSError as exc:
                raise ValueError(f"Cannot bind the local engine to {values['server_host']}:{values['server_port']}: {exc}") from exc
        display = format_command(argv)
        self.command_var.set(display)
        self._active_command_snapshot = self.command_panel.record(argv, kind, asdict(self.selected_model),
                                                                 asdict(self.selected_runtime), self.collect_settings(), "En ejecución")
        if kind == "inference":
            self._inference_raw = []
            self._render_inference()
        self.log.insert("end", f"\n=== {kind} {datetime.now().isoformat(timespec='seconds')} ===\n$ {display}\n\n")
        self.log.see("end")
        self.active_kind = kind
        self._job_stop_reason = ""
        if kind == "server":
            self.server_values = self.collect_settings().copy()
            self.server_model = self.selected_model
            self.server_runtime = self.selected_runtime
        self.benchmark = benchmark
        for button in self.action_buttons:
            button.state(["disabled"])
        try:
            self.runner.start(argv, cwd=self.selected_runtime.directory)
            if kind != "server":
                self._job_timer = self.after(self.job_timeout_var.get() * 1000, self._job_timeout)
        except Exception:
            self.active_kind = None
            self.benchmark = None
            for button in self.action_buttons:
                button.state(["!disabled"])
            raise
        self.status_var.set(f"Running {kind}...")
        return True

    def _job_timeout(self):
        self._job_timer = None
        if self.runner.running:
            self._job_stop_reason = "Job time limit exceeded"
            if self.benchmark is not None:
                self.benchmark.status = "timeout"
                self.benchmark.output.append("\n[timeout] Job time limit exceeded\n")
            self.runner.stop()


    def run_inference(self):
        try:
            argv = self.build_inference()
            values = self.collect_settings()
            session = BenchmarkSession(Path(values["root"]), asdict(self.selected_model), asdict(self.selected_runtime),
                                       argv[:], {}, settings=values.copy(), experiment="inference")
            self._run(argv, "inference", session)
        except Exception as e:
            messagebox.showerror(APP_NAME, str(e))


    def run_benchmark(self):
        if self.active_kind is not None or self.runner.running or self.lab.running:
            messagebox.showwarning(APP_NAME, "A process is already running.")
            return
        try:
            argv = self.build_bench()
            values = self.collect_settings()
            session = BenchmarkSession(
                root=Path(values["root"]), model=asdict(self.selected_model),
                runtime=asdict(self.selected_runtime), command=argv[:],
                settings=values.copy(),
                parameters={"prompt_tokens": values["bench_prompt"], "generation_tokens": values["bench_gen"],
                            "repetitions": values["bench_reps"], "gpu_layers": values["ngl"], "threads": values["threads"]},
            )
            self._run(argv, "benchmark", session)
        except (ValueError, OSError, RuntimeError) as exc:
            messagebox.showerror(APP_NAME, str(exc))


    def run_server(self):
        try:
            self._run(self.build_server(), "server")
        except Exception as e:
            messagebox.showerror(APP_NAME, str(e))


    def stop_process(self):
        self.plan.stop()
        if self.benchmark is not None and not self.benchmark.status:
            self.benchmark.status = "cancelled"
        self.chat.stop()
        self.evaluation.stop()
        self.lab.stop()
        if self.runner.running:
            self.status_var.set("Stopping...")
            self.runner.stop()


    def _drain_queue(self):
        # Limit work per tick so a fast subprocess cannot monopolize the Tk event loop.
        deadline = time.monotonic() + 0.012
        try:
            while time.monotonic() < deadline:
                kind, payload = self.msgq.get_nowait()
                if kind.startswith("evaluation_"):
                    self.evaluation.event(kind, payload)
                    continue
                if kind.startswith("chat_"):
                    self.chat.event(kind, payload)
                    continue
                if kind.startswith("lab_"):
                    self.lab.event(kind, payload)
                    continue
                if kind == "telemetry":
                    self.telemetry_strip.sample(payload)
                    self.plan.sample(payload)
                    self.monitor_panel.sample(payload)
                    try:
                        reason = thermal_violation(payload, self.thermal_limit_var.get()) if time.monotonic() - payload["clock"] <= 6 else ""
                    except tk.TclError:
                        reason = ""
                    if reason and (self.runner.running or self.lab.runner.running):
                        self.log.insert("end", f"\n[stability] {reason}\n")
                        self._job_stop_reason = reason
                        for session in (self.benchmark, self.lab.session):
                            if session is not None:
                                session.status = "thermal_stop"
                                session.output.append(f"\n[stability] {reason}\n")
                        self.stop_process()
                    for session in (self.benchmark, self.lab.session):
                        if session is not None and payload["clock"] >= session.clock_started and len(session.telemetry) < 3600:
                            session.telemetry.append(payload)
                    if payload.get("gpus"):
                        gpu = payload["gpus"][0]
                        self.gpu_var.set(f"GPU {gpu['utilization'] or 0:.0f}% · VRAM {gpu['used_mib'] or 0:.0f}/{gpu['total_mib'] or 0:.0f} MiB")
                    continue
                if kind == "log":
                    text = str(payload)
                    self.log.insert("end", text)
                    if self.benchmark is not None:
                        self.benchmark.output.append(text)
                    if self.active_kind == "inference":
                        self._inference_raw.append(text)
                        self._render_inference()
                elif kind == "done":
                    code = int(payload)
                    self.command_panel.finish(self._active_command_snapshot, code)
                    if self._job_timer is not None:
                        self.after_cancel(self._job_timer)
                        self._job_timer = None
                    self.status_var.set(self._job_stop_reason or f"Process exited with code {code}")
                    self.log.insert("end", f"\n[exit {code}]\n")
                    if self.benchmark is not None:
                        self._save_bench_result(code)
                    self.benchmark = None
                    self.active_kind = None
                    for button in self.action_buttons:
                        button.state(["!disabled"])
                elif kind == "gpu":
                    self._gpu_pending = False
                    self.gpu_var.set("GPU: " + (str(payload).splitlines() or ["No result"])[0])
                    self.system_text.delete("1.0", "end")
                    self.system_text.insert("end", str(payload))
                elif kind == "scan":
                    self._apply_scan(payload)
        except queue.Empty:
            pass
        self.log.see("end")
        line_count = int(self.log.index("end-1c").split(".")[0])
        if line_count > 10000:
            self.log.delete("1.0", f"{line_count - 10000}.0")
        self.after(50, self._drain_queue)

    def _render_inference(self):
        answer, thoughts = split_inference_output("".join(self._inference_raw))
        for widget, text in ((self.inference_response, answer), (self.inference_thoughts, thoughts)):
            widget.configure(state="normal")
            widget.delete("1.0", "end")
            widget.insert("1.0", text[-200000:])
            widget.see("end")
            widget.configure(state="disabled")


    def _save_bench_result(self, exit_code):
        try:
            path = self.benchmark.save(exit_code)
            self.log.insert("end", f"[benchmark saved] {path}\n")
            self.lab.refresh_history()
        except OSError as exc:
            self.log.insert("end", f"[benchmark save failed] {exc}\n")
            messagebox.showerror(APP_NAME, f"Could not save benchmark: {exc}")


    def probe_gpu(self):
        if self._gpu_pending:
            return
        self._gpu_pending = True
        self.gpu_var.set("GPU: probing...")
        def worker():
            self.msgq.put(("gpu", probe_nvidia_gpu()))
        threading.Thread(target=worker, daemon=True).start()


    def show_paths(self):
        self.system_text.delete("1.0", "end")
        lines = [
            f"Workspace: {self.root_var.get()}",
            f"Settings:  {SETTINGS_PATH}",
            f"Model:     {self.selected_model.path if self.selected_model else '-'}",
            f"Runtime:   {self.selected_runtime.directory if self.selected_runtime else '-'}",
        ]
        self.system_text.insert("end", "\n".join(lines))


    def on_close(self):
        if self._closing:
            return
        if (self.runner.running or self.lab.running) and not messagebox.askyesno(APP_NAME, "A process is running. Stop it and exit?"):
            return
        try:
            self.save_settings()
        except (ValueError, OSError) as exc:
            messagebox.showerror(APP_NAME, f"Could not save settings: {exc}")
            return
        self._closing = True
        self.plan.stop()
        self.runner.stop()
        self.lab.stop()
        self.chat.stop()
        self.monitor.stop()
        self.evaluation.stop()
        self._finish_close()

    def _finish_close(self):
        if self.runner.running or self.lab.runner.running or self.plan.running:
            self.after(50, self._finish_close)
        else:
            self.destroy()

