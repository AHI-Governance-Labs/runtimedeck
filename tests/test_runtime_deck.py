"""Regression checks for discovery, persistence, commands, events and cancellation."""

import json
import queue
import sys
import tempfile
import time
import tkinter as tk
import unittest
from pathlib import Path
from unittest.mock import patch

from runtime_deck_core.benchmarks import BenchmarkSession
from runtime_deck_core.commands import build_command
from runtime_deck_core.discovery import discover_workspace
from runtime_deck_core.models import ModelItem, RuntimeItem
from runtime_deck_core.processes import ProcessRunner
from runtime_deck_core.settings import DEFAULTS, SettingsStore, validate_settings


class CoreTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        model_path = self.root / "models" / "nested" / "model with spaces.gguf"
        model_path.parent.mkdir(parents=True)
        model_path.write_bytes(b"GGUF")
        self.model = ModelItem(str(model_path), model_path.name, "gguf", 4)
        directory = self.root / "runtimes" / "cuda"
        directory.mkdir(parents=True)
        for name in ("llama-cli.exe", "llama-bench.exe", "llama-server.exe"):
            (directory / name).touch()
        self.runtime = RuntimeItem(str(directory), "cuda", cli=str(directory / "llama-cli.exe"),
                                   bench=str(directory / "llama-bench.exe"), server=str(directory / "llama-server.exe"))

    def test_discovery_groups_capabilities_and_nested_models(self):
        models, runtimes, warnings = discover_workspace(self.root)
        self.assertEqual(models, [self.model])
        self.assertEqual(len(runtimes), 1)
        self.assertEqual(runtimes[0].capabilities, "infer, bench, server")
        self.assertEqual(warnings, [])

    def test_missing_workspace_is_explicit(self):
        with self.assertRaises(ValueError):
            discover_workspace(self.root / "missing")

    def test_settings_roundtrip_and_invalid_fields_fall_back(self):
        store = SettingsStore(self.root / "preferences" / "settings.json")
        values = dict(DEFAULTS, ctx=4096, prompt="¿Qué modelo estás usando?")
        store.save(values)
        self.assertEqual(store.load(), values)
        self.assertEqual(list(store.path.parent.glob("*.tmp")), [])
        store.path.write_text(json.dumps({"ctx": "bad", "threads": 4, "flash": "false"}))
        loaded = store.load()
        self.assertEqual(loaded["ctx"], DEFAULTS["ctx"])
        self.assertEqual(loaded["threads"], 4)
        self.assertIs(loaded["flash"], True)

    def test_corrupt_settings_are_recoverable(self):
        store = SettingsStore(self.root / "settings.json")
        for content in ("{", "null", "[]", '"text"'):
            store.path.write_text(content)
            self.assertEqual(store.load(), DEFAULTS)

    def test_invalid_numbers_and_port_are_rejected(self):
        for key, value in (("ctx", 1.5), ("temp", float("nan")), ("top_p", 2),
                           ("server_port", 65536), ("threads", True)):
            with self.subTest(key=key), self.assertRaises(ValueError):
                validate_settings(dict(DEFAULTS, **{key: value}))

    def test_inference_arguments_preserve_prompt_and_paths(self):
        values = dict(DEFAULTS, prompt='hello "world" & test', mmap=False)
        argv = build_command("inference", self.model, self.runtime, values)
        self.assertEqual(argv, [self.runtime.cli, "-m", self.model.path,
            "-c", "8192", "-ngl", "99", "-t", str(DEFAULTS["threads"]),
            "-b", "512", "-ub", "256", "-n", "256", "--temp", "0.7",
            "--top-p", "0.95", "--top-k", "40", "--seed", "-1", "-p",
            values["prompt"], "--single-turn", "--simple-io", "--perf", "--flash-attn", "on", "--no-mmap"])

    def test_benchmark_and_server_arguments(self):
        benchmark = build_command("benchmark", self.model, self.runtime, DEFAULTS)
        self.assertEqual(benchmark, [self.runtime.bench, "-m", self.model.path,
            "-p", "512", "-n", "128", "-r", "3", "-ngl", "99", "-t", str(DEFAULTS["threads"]),
            "-o", "json", "-b", "512", "-ub", "256", "-fa", "on"])
        server = build_command("server", self.model, self.runtime, dict(DEFAULTS, flash=False))
        self.assertIn("127.0.0.1", server)
        self.assertIn("8080", server)
        self.assertNotIn("--flash-attn", server)

    def test_unsupported_or_missing_model_and_capability(self):
        other = ModelItem(self.model.path, self.model.name, "safetensors", 4)
        for model, runtime in ((other, self.runtime), (None, self.runtime),
                               (self.model, None), (self.model, RuntimeItem("", "empty"))):
            with self.assertRaises(ValueError):
                build_command("inference", model, runtime, DEFAULTS)
        Path(self.model.path).unlink()
        with self.assertRaises(ValueError):
            build_command("inference", self.model, self.runtime, DEFAULTS)

    def test_benchmark_metadata_and_unique_filenames(self):
        session = BenchmarkSession(self.root, {"name": "original.gguf"}, {"label": "original"},
                                   ["bench", "-p", "512"], {"prompt_tokens": 512})
        session.output.append("result\n")
        path = session.save(0)
        metadata = json.loads(path.with_suffix(".json").read_text())
        self.assertEqual(path.read_text(), "result\n")
        self.assertEqual(metadata["model"]["name"], "original.gguf")
        self.assertEqual(metadata["command"], session.command)
        other = BenchmarkSession(self.root, session.model, session.runtime, [], {})
        self.assertNotEqual(path, other.save(1))


class ProcessTests(unittest.TestCase):
    def test_output_and_exit_code(self):
        events = queue.Queue()
        runner = ProcessRunner(lambda line: events.put(("log", line)), lambda code: events.put(("done", code)))
        runner.start([sys.executable, "-u", "-c", "print('working'); raise SystemExit(7)"])
        runner.thread.join(timeout=8)
        self.assertFalse(runner.running)
        self.assertEqual(events.get(timeout=1), ("log", "working\n"))
        self.assertEqual(events.get(timeout=1), ("done", 7))

    def test_duplicate_start_and_immediate_stop(self):
        events = queue.Queue()
        runner = ProcessRunner(lambda line: None, events.put)
        try:
            runner.start([sys.executable, "-c", "import time; time.sleep(30)"])
            self.assertTrue(runner.running)
            with self.assertRaises(RuntimeError):
                runner.start([sys.executable, "-c", "pass"])
            start = time.monotonic()
            runner.stop()
            self.assertLess(time.monotonic() - start, 0.5)
            runner.thread.join(timeout=8)
            self.assertFalse(runner.running)
            events.get(timeout=1)
        finally:
            runner.stop()


class GuiTests(unittest.TestCase):
    def test_fresh_thermal_sample_stops_owned_job_but_stale_sample_does_not(self):
        from runtime_deck_core.app import RuntimeDeck
        with patch.object(SettingsStore, "load", return_value=DEFAULTS.copy()):
            app = RuntimeDeck()
        app.withdraw()
        try:
            for callback in app.tk.splitlist(app.tk.call("after", "info")):
                app.after_cancel(callback)
            app.runner.start([sys.executable, "-c", "import time; time.sleep(30)"])
            app.active_kind = "inference"
            gpu = {"name": "Test sensor", "temperature": 90, "used_mib": 1, "total_mib": 2,
                   "utilization": 10, "power_watts": 5}
            app.msgq.put(("telemetry", {"clock": time.monotonic() - 20, "gpus": [gpu]}))
            app._drain_queue()
            self.assertTrue(app.runner.running)
            app.msgq.put(("telemetry", {"clock": time.monotonic(), "gpus": [gpu]}))
            app._drain_queue()
            app.runner.thread.join(8)
            self.assertFalse(app.runner.running)
            self.assertIn("Thermal guard", app._job_stop_reason)
        finally:
            app.runner.stop()
            if app.runner.thread:
                app.runner.thread.join(8)
            app.monitor.stop()
            for callback in app.tk.splitlist(app.tk.call("after", "info")):
                app.after_cancel(callback)
            app.destroy()

    def test_dark_widgets_validation_and_shared_event_queue(self):
        from runtime_deck_core.app import RuntimeDeck
        with patch.object(SettingsStore, "load", return_value=DEFAULTS.copy()):
            app = RuntimeDeck()
        app.withdraw()
        try:
            # Prevent automatic scans and hardware probes; fixtures drive event delivery.
            for callback in app.tk.splitlist(app.tk.call("after", "info")):
                app.after_cancel(callback)
            self.assertEqual(app.log.cget("background"), "#101824")
            self.assertEqual(app.system_text.cget("background"), "#101824")
            self.assertEqual(len(app.action_buttons), 7)
            self.assertIsNot(app.model_tree.master, app.model_tree.master.master)
            self.assertIn(app.model_tree, app.model_tree.master.winfo_children())
            self.assertEqual(app.model_tree.winfo_parent(), str(app.model_tree.master))
            self.assertEqual(app.log.winfo_parent(), str(app.log.master))
            app.setvar(app.threads_var._name, "1.5")
            with self.assertRaises(ValueError):
                app.collect_settings()
            app.threads_var.set(4)
            self.assertEqual(app.collect_settings()["threads"], 4)
            app.msgq.put(("gpu", "Test GPU"))
            app.msgq.put(("log", "output survives gpu event\n"))
            app._drain_queue()
            self.assertEqual(app.gpu_var.get(), "GPU: Test GPU")
            self.assertIn("output survives", app.log.get("1.0", "end"))
            app._apply_scan((app._scan_generation, ([], [], []), None))
            self.assertIsNone(app.selected_model)
            self.assertIsNone(app.selected_runtime)
        finally:
            for callback in app.tk.splitlist(app.tk.call("after", "info")):
                app.after_cancel(callback)
            app.destroy()


if __name__ == "__main__":
    unittest.main()
