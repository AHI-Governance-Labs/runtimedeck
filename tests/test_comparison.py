import csv
import tempfile
import unittest
from pathlib import Path

from runtime_deck_core.comparison import comparable_throughput
from runtime_deck_core.history import export_csv


class ComparisonTests(unittest.TestCase):
    def record(self, **changes):
        base = {"model": {"path": "model.gguf", "size": 100}, "status": "success", "exit_code": 0,
                "parameters": {"prompt_tokens": 64, "generation_tokens": 16, "repetitions": 2},
                "metrics": {"generation_tps": 30}}
        return dict(base, **changes)

    def test_groups_require_same_model_and_workload(self):
        reference = self.record()
        other_workload = self.record(parameters={"prompt_tokens": 128, "generation_tokens": 16, "repetitions": 2})
        other_model = self.record(model={"path": "another.gguf", "size": 100})
        candidate = self.record(metrics={"generation_tps": 32})
        self.assertEqual(comparable_throughput([reference, other_workload, other_model, candidate]), [reference, candidate])
        self.assertEqual(comparable_throughput([reference, other_workload], other_workload), [other_workload])

    def test_unknown_failed_mismatched_and_nonfinite_are_excluded(self):
        records = [self.record(status="failed"), self.record(exit_code=1), self.record(parameters={}),
                   self.record(metrics={"generation_tps": float("nan")}),
                   self.record(parameter_verification={"mismatches": ["threads"]})]
        self.assertEqual(comparable_throughput(records), [])

    def test_csv_includes_answer_quality(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "results.csv"
            export_csv([self.record(metrics={"quality_pass_pct": 50, "cases_total": 2,
                                            "cases_completed": 2, "cases_passed": 1})], path)
            with path.open(encoding="utf-8-sig", newline="") as stream:
                row = next(csv.DictReader(stream))
            self.assertEqual(row["quality_pass_pct"], "50")
            self.assertEqual(row["cases_passed"], "1")
