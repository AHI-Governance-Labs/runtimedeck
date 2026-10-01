"""Objective decisions, catalog labels, prompt editing and command restoration."""

import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from runtime_deck_core.catalog import model_label
from runtime_deck_core.inference_output import split_inference_output
from runtime_deck_core.models import ModelItem, RuntimeItem
from runtime_deck_core.plans import assess_plan, atomic_json, validate_criteria
from runtime_deck_core.settings import DEFAULTS, SettingsStore


class ObjectiveTests(unittest.TestCase):
    def document(self):
        return {"status": "finished", "criteria": {"generation_tps": 25, "quality_pass_pct": 90,
                "temperature_c": 85, "memory_mib": 11264, "stability_runs": 2},
                "results": {"stability": [{"status": "success", "exit_code": 0, "metrics": {"generation_tps": speed}}
                                          for speed in (30, 28)],
                            "answers": [{"status": "success", "metrics": {"cases_total": 10, "cases_completed": 10, "quality_pass_pct": 100}}]},
                "telemetry": [{"gpus": [{"temperature": 70, "used_mib": 8000}]}]}

    def test_all_criteria_require_evidence(self):
        document = self.document()
        self.assertEqual(assess_plan(document)["state"], "cumple")
        document["telemetry"] = []
        self.assertEqual(assess_plan(document)["state"], "pendiente")
        document["status"] = "cancelled"
        self.assertEqual(assess_plan(document)["state"], "incompleto")

    def test_quality_failure_is_preserved_despite_successful_process(self):
        document = self.document()
        document["results"]["answers"][0]["metrics"]["quality_pass_pct"] = 66.7
        self.assertEqual(assess_plan(document)["state"], "no cumple")

    def test_partial_quality_and_nonfinite_speed_cannot_pass(self):
        document = self.document()
        document["results"]["answers"][0]["metrics"]["cases_completed"] = 9
        self.assertEqual(assess_plan(document)["state"], "pendiente")
        document = self.document()
        document["results"]["stability"][1]["metrics"]["generation_tps"] = float("nan")
        self.assertEqual(assess_plan(document)["state"], "pendiente")

    def test_uses_slowest_stability_run_not_fastest_candidate(self):
        document = self.document()
        document["results"]["stability"][1]["metrics"]["generation_tps"] = 20
        self.assertEqual(assess_plan(document)["state"], "no cumple")

    def test_invalid_and_fractional_criteria_rejected(self):
        criteria = self.document()["criteria"]
        for key, value in (("stability_runs", 2.5), ("generation_tps", "nan"), ("quality_pass_pct", 101)):
            with self.subTest(key=key), self.assertRaises(ValueError):
                validate_criteria(dict(criteria, **{key: value}))

    def test_atomic_plan_does_not_leave_partial_json(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "plan.json"
            atomic_json(path, self.document())
            self.assertEqual(json.loads(path.read_text())["status"], "finished")
            self.assertEqual(len(list(path.parent.iterdir())), 1)

    def test_catalog_normalizes_display_and_separates_auxiliaries(self):
        model = ModelItem("G:/Runtimes/models/bonsai/27B/Ternary-Bonsai-2-27B-PQ2_0.gguf", "Ternary-Bonsai-2-27B-PQ2_0.gguf", "gguf", 10)
        label = model_label(model, "G:/Runtimes")
        self.assertEqual(label["family"], "Bonsai")
        self.assertEqual(label["title"], "Bonsai 2 27B")
        self.assertEqual(label["quantization"], "PQ2_0")
        auxiliary = ModelItem(model.path, "Ternary-Bonsai-2-27B-mmproj-Q8_0.gguf", "gguf", 10)
        self.assertEqual(model_label(auxiliary, "G:/Runtimes")["role"], "Auxiliares")

    def test_inference_response_separates_reasoning_and_diagnostics(self):
        raw = "Loading...\n> Pregunta\n[Start thinking]\nComprobar\n[End thinking]\n19\n[ Prompt: 10 t/s | Generation: 30 t/s ]\nExiting...\n"
        self.assertEqual(split_inference_output(raw), ("19", "Comprobar"))
        self.assertEqual(split_inference_output("Loading..."), ("", ""))
        self.assertEqual(split_inference_output("> Pregunta\n[Start thinking]\nComprobar"), ("", "Comprobar"))


class WorkbenchGuiTests(unittest.TestCase):
    def test_prompt_catalog_command_restore_and_visible_telemetry(self):
        from runtime_deck_core.app import RuntimeDeck
        with tempfile.TemporaryDirectory() as directory, patch.object(SettingsStore, "load", return_value=dict(DEFAULTS, root=directory)):
            app = RuntimeDeck()
            app.withdraw()
            try:
                for callback in app.tk.splitlist(app.tk.call("after", "info")):
                    app.after_cancel(callback)
                root = Path(directory)
                model_path = root / "models" / "bonsai" / "Bonsai-4B-Q1_0.gguf"
                model_path.parent.mkdir(parents=True)
                model_path.write_bytes(b"GGUF")
                executable = root / "llama-cli.exe"
                executable.touch()
                model = ModelItem(str(model_path), model_path.name, "gguf", 4)
                runtime = RuntimeItem(str(root), "test", cli=str(executable))
                app._apply_scan((app._scan_generation, ([model], [runtime], []), None))
                app.prompt_var.set("Línea 1\nLínea 2")
                self.assertEqual(app.prompt_editor.text.get("1.0", "end-1c"), "Línea 1\nLínea 2")
                app.prompt_editor.text.insert("end-1c", "\nLínea 3")
                app.prompt_editor._changed()
                self.assertIn("Línea 3", app.collect_settings()["prompt"])
                self.assertEqual(app.model_tree.item("m0", "text"), "Bonsai 4B")
                self.assertNotEqual(app.model_tree.parent("m0"), "")
                app.preview_inference()
                snapshot = app.command_panel.current
                values = dict(snapshot["settings"], threads=3)
                app.command_panel.editor.delete("1.0", "end")
                app.command_panel.editor.insert("1.0", json.dumps(values))
                self.assertTrue(app.command_panel.restore())
                self.assertEqual(app.threads_var.get(), 3)
                self.assertNotEqual(snapshot["settings"]["threads"], 3)
                app.telemetry_strip.sample({"clock": time.monotonic(), "gpus": [{"name": "Test", "used_mib": 1024,
                                           "total_mib": 12288, "temperature": None, "utilization": 0, "power_watts": None}]})
                self.assertEqual(app.telemetry_strip.values["temperature"].get(), "N/D")
                self.assertEqual(app.telemetry_strip.values["utilization"].get(), "0 %")
                self.assertIs(app.telemetry_strip.master, app)
            finally:
                app.monitor.stop()
                for callback in app.tk.splitlist(app.tk.call("after", "info")):
                    app.after_cancel(callback)
                app.destroy()
