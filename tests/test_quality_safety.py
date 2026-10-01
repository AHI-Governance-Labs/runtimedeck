import io
import json
import tempfile
import unittest
from pathlib import Path

from runtime_deck_core.chat import sse_events
from runtime_deck_core.datasets import freeze_corpus
from runtime_deck_core.evaluation import check_answer, load_cases
from runtime_deck_core.safety import thermal_violation, verify_reported_parameters


class QualitySafetyTests(unittest.TestCase):
    def test_sse_unicode_and_done(self):
        raw = ': heartbeat\n\ndata: {"choices":[{"delta":{"content":"¿Hola?"}}]}\n\ndata: [DONE]\n\n'
        events = list(sse_events(io.BytesIO(raw.encode("utf-8"))))
        self.assertEqual(events[0]["choices"][0]["delta"]["content"], "¿Hola?")
        self.assertEqual(len(events), 1)

    def test_answer_checks(self):
        for check, expected, answer in (("exact", "19", " 19 "), ("contains", "hola", "hola colega"),
                                        ("regex", r"^\d+$", "42"), ("json", {"ok": True}, '{"ok":true}')):
            self.assertTrue(check_answer({"check": check, "expected": expected}, answer))
        self.assertFalse(check_answer({"check": "json", "expected": {"ok": True}}, '```json\n{"ok":true}\n```'))
        self.assertFalse(check_answer({"check": "exact", "expected": "19"}, "19 because..."))

    def test_invalid_dataset_rejected_before_execution(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "cases.jsonl"
            path.write_text('{"prompt":"question", "check":"execute", "expected":"anything"}\n')
            with self.assertRaises(ValueError):
                load_cases(path)

    def test_frozen_corpus_remains_unchanged_and_hash_is_reproducible(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "source.txt"
            source.write_text("original corpus", encoding="utf-8")
            first = freeze_corpus(source, root)
            second = freeze_corpus(source, root)
            self.assertEqual(first["sha256"], second["sha256"])
            source.write_text("changed corpus", encoding="utf-8")
            self.assertEqual(Path(first["path"]).read_text(), "original corpus")
            with self.assertRaises(InterruptedError):
                freeze_corpus(source, root, lambda: True)
            self.assertEqual(len(list((root / "benchmarks" / "corpora").iterdir())), 1)

    def test_thermal_guard_limits_and_missing_sensor(self):
        sample = {"gpus": [{"name": "test", "temperature": 85}]}
        self.assertIn("Thermal guard", thermal_violation(sample, 85))
        self.assertFalse(thermal_violation(sample, 0))
        self.assertFalse(thermal_violation({"gpus": [{"name": "test", "temperature": None}]}, 85))

    def test_configuration_claim_requires_runtime_report(self):
        settings = {"threads": 8, "batch": 512, "ubatch": 256, "ngl": 99, "flash": True}
        self.assertEqual(verify_reported_parameters(settings, {})["state"], "not_reported")
        row = {"n_threads": 4, "n_batch": 512, "n_ubatch": 256, "n_gpu_layers": 99, "flash_attn": 0}
        verification = verify_reported_parameters(settings, {"benchmark_rows": [row]})
        self.assertEqual(verification["state"], "mismatch")
        self.assertEqual({item["parameter"] for item in verification["mismatches"]}, {"threads", "flash"})


if __name__ == "__main__":
    unittest.main()
