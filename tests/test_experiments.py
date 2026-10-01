import json
import tempfile
import unittest
from pathlib import Path

from runtime_deck_core.discovery import discover_workspace
from runtime_deck_core.experiments import best_result, numeric_axis, scaling_plan, stability_plan, tuning_plan
from runtime_deck_core.metrics import explain_failure, parse_metrics
from runtime_deck_core.settings import DEFAULTS


class ExperimentTests(unittest.TestCase):
    def test_mixed_backend_logs_and_json(self):
        raw = 'CUDA [device 0]\n' + json.dumps([
            {"n_prompt": 512, "n_gen": 0, "avg_ts": 294.2, "stddev_ts": 3},
            {"n_prompt": 0, "n_gen": 128, "avg_ts": 30.9, "stddev_ts": 0.2}])
        result = parse_metrics(raw)
        self.assertEqual(result["prompt_tps"], 294.2)
        self.assertEqual(result["generation_tps"], 30.9)
        self.assertEqual(result["generation_stddev"], 0.2)

    def test_corrupt_json_cannot_fabricate_metrics(self):
        result = parse_metrics('CUDA [0]\n{"avg_ts": 3, broken}\nPPL = nan')
        self.assertNotIn("generation_tps", result)
        self.assertNotIn("perplexity", result)

    def test_quality_metric_and_oom_diagnosis(self):
        self.assertEqual(parse_metrics("Final estimate: PPL = 7.21 +/- 0.1")["perplexity"], 7.21)
        self.assertIn("memory", explain_failure("CUDA out of memory", 1))

    def test_grid_is_bounded_and_workload_constant(self):
        trials = tuning_plan(DEFAULTS, "99", "4,8", "256,512", 4)
        self.assertEqual(len(trials), 4)
        for trial in trials:
            self.assertEqual(trial.values["bench_prompt"], DEFAULTS["bench_prompt"])
            self.assertLessEqual(trial.values["ubatch"], trial.values["batch"])
        with self.assertRaises(ValueError):
            tuning_plan(DEFAULTS, "0,99", "4,8", "256,512", 4)

    def test_invalid_axes_and_duplicate_candidates(self):
        self.assertEqual(numeric_axis("4,4,8", "threads"), [4, 8])
        for axis in ("", "a", "1.5", "0"):
            with self.assertRaises(ValueError):
                numeric_axis(axis, "threads")

    def test_failed_and_cancelled_trials_cannot_win(self):
        records = [
            {"status": "failed", "exit_code": 1, "metrics": {"generation_tps": 1000}},
            {"status": "cancelled", "exit_code": 0, "metrics": {"generation_tps": 2000}},
            {"status": "success", "exit_code": 0, "metrics": {"generation_tps": 31}},
        ]
        self.assertIs(best_result(records), records[-1])

    def test_scaling_and_stability_design(self):
        self.assertEqual([t.values["bench_prompt"] for t in scaling_plan(DEFAULTS, "128,512")], [128, 512])
        self.assertEqual(len(stability_plan(DEFAULTS, 3)), 3)

    def test_projector_never_becomes_default_model(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "models").mkdir()
            for name in ("a-mmproj.gguf", "z-model.gguf"):
                (root / "models" / name).touch()
            models, _, _ = discover_workspace(root)
            self.assertTrue(models[0].runnable)
            self.assertTrue(models[1].is_auxiliary)


if __name__ == "__main__":
    unittest.main()
