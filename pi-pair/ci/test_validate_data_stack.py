"""Validator accepts pi-pair/data and rejects the broken overlap fixture."""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

CI = Path(__file__).resolve().parent
DATA = CI.parent / "data"
BROKEN = CI / "fixtures" / "broken_overlap"
SCRIPT = CI / "validate_data_stack.py"


def _run(data: Path, report: Path) -> tuple[int, dict, str]:
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--data", str(data), "--report", str(report)],
        capture_output=True,
        text=True,
        check=False,
    )
    body = json.loads(report.read_text(encoding="utf-8"))
    return result.returncode, body, result.stderr


class ValidateDataStack(unittest.TestCase):
    def test_fixture_data_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = Path(tmp) / "report.json"
            code, body, err = _run(DATA, report)
        self.assertEqual(code, 0, err)
        self.assertTrue(body["ok"])
        self.assertEqual(body["errors"], [])
        self.assertEqual(
            body["counts"],
            {"canned": 111, "sft_chat": 20, "sft_alpaca": 6, "preference": 8, "eval_heldout": 15},
        )
        self.assertEqual(body["locks"]["generate"], ["pi4"])
        self.assertEqual(body["locks"]["dataset_and_train"], ["pi3"])
        self.assertEqual(body["locks"]["health"], ["pi2"])
        self.assertEqual(body["locks"]["search"], ["pi2"])
        self.assertIs(body["locks"]["train_then_delete"], True)
        self.assertIs(body["locks"]["weak_gen"], False)

    def test_broken_fixture_schemas_match_the_good_tree(self):
        rels = [
            "dataset_info.json",
            "seed/dataset_info.json",
            "seed/schemas/dataset_info.json",
            "seed/schemas/canned.schema.json",
            "seed/schemas/sft_chat.schema.json",
            "seed/schemas/sft_alpaca.schema.json",
            "seed/schemas/preference.schema.json",
            "seed/schemas/eval_heldout.schema.json",
        ]
        for rel in rels:
            good = (DATA / rel).read_bytes()
            bad = (BROKEN / rel).read_bytes()
            self.assertEqual(good, bad, rel)

    def test_broken_overlap_fixture_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = Path(tmp) / "report.json"
            code, body, err = _run(BROKEN, report)
        self.assertNotEqual(code, 0)
        self.assertFalse(body["ok"])
        self.assertIn("eval_overlaps_canned", err)
        codes = {item["code"] for item in body["errors"]}
        self.assertEqual(codes, {"eval_overlaps_canned"})

    def test_missing_answer_fails_schema(self):
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "data"
            shutil.copytree(DATA, dest)
            seed = dest / "canned" / "canned_seed.jsonl"
            lines = seed.read_text(encoding="utf-8").splitlines()
            row = json.loads(lines[0])
            del row["answer"]
            lines[0] = json.dumps(row)
            seed.write_text("\n".join(lines) + "\n", encoding="utf-8")
            report = Path(tmp) / "report.json"
            code, body, _err = _run(dest, report)
        self.assertNotEqual(code, 0)
        codes = {item["code"] for item in body["errors"]}
        self.assertIn("schema", codes)
        self.assertIn("canned_map_mismatch", codes)

    def test_weak_gen_lock_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "data"
            shutil.copytree(DATA, dest)
            manifest_path = dest / "BUILD_MANIFEST.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["locks"]["weak_gen"] = True
            manifest["locks"]["generate"] = ["pi3"]
            manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
            report = Path(tmp) / "report.json"
            code, body, _err = _run(dest, report)
        self.assertNotEqual(code, 0)
        codes = {item["code"] for item in body["errors"]}
        self.assertEqual(codes, {"lock_generate", "lock_weak_gen"})

    def test_canned_map_keys_must_already_be_normalized(self):
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "data"
            shutil.copytree(DATA, dest)
            seed = dest / "canned" / "canned_seed.jsonl"
            lines = seed.read_text(encoding="utf-8").splitlines()
            row = json.loads(lines[0])
            old = row["input"]
            row["input"] = old + "."
            lines[0] = json.dumps(row)
            seed.write_text("\n".join(lines) + "\n", encoding="utf-8")
            map_path = dest / "canned" / "canned_map.json"
            table = json.loads(map_path.read_text(encoding="utf-8"))
            table[old + "."] = table.pop(old)
            map_path.write_text(json.dumps(table, indent=2) + "\n", encoding="utf-8")
            report = Path(tmp) / "report.json"
            code, body, _err = _run(dest, report)
        self.assertNotEqual(code, 0)
        codes = {item["code"] for item in body["errors"]}
        self.assertIn("canned_map_mismatch", codes)
        detail = " ".join(item["detail"] for item in body["errors"])
        self.assertIn("must already be normalized", detail)

    def test_trailing_dot_cannot_hide_an_eval_overlap(self):
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "data"
            shutil.copytree(DATA, dest)
            heldout = dest / "seed" / "eval_heldout" / "eval_heldout.jsonl"
            row = json.loads(heldout.read_text(encoding="utf-8").splitlines()[0])
            row["id"] = "syn-dot"
            row["input"] = "hi."
            with heldout.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(row) + "\n")
            manifest_path = dest / "BUILD_MANIFEST.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["counts"]["eval_heldout"] = 16
            manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
            report = Path(tmp) / "report.json"
            code, body, _err = _run(dest, report)
        self.assertNotEqual(code, 0)
        codes = {item["code"] for item in body["errors"]}
        self.assertIn("eval_overlaps_canned", codes)


if __name__ == "__main__":
    unittest.main()
