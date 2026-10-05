"""Registry, queue bounds, and the pi3 train-then-delete cycle."""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pair.canned import _semantic_table, lookup, normalize_key
from pair.chat import chat_ollama
from pair.guard import may_generate
from pair.lifecycle import GateError, _prepare, post_train
from pair.queue import QUEUE_BOUND, append_row, apply_label, note_exchange
from pair.registry import RegistryError, require_registered
from pair.yaml_lite import load_path


class Flywheel(unittest.TestCase):
    def setUp(self):
        self._env = {
            key: os.environ.get(key)
            for key in (
                "PI_PAIR_DATA",
                "PI_PAIR_ROLE",
                "PI_PAIR_ADAPTERS",
                "PI_PAIR_TRAIN_CONFIG",
                "PI_PAIR_NAME",
                "PI_PAIR_LABEL_HIGH_WATER_BYTES",
                "PI_PAIR_DISK_HIGH_WATER",
                "HF_TOKEN",
                "KAGGLE_API_TOKEN",
            )
        }
        os.environ.pop("HF_TOKEN", None)
        os.environ.pop("KAGGLE_API_TOKEN", None)
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        os.environ["PI_PAIR_ROLE"] = "dataset"
        os.environ["PI_PAIR_DATA"] = str(self.base / "data")
        os.environ["PI_PAIR_ADAPTERS"] = str(self.base / "adapters")
        os.environ.pop("PI_PAIR_NAME", None)
        os.environ.pop("PI_PAIR_TRAIN_CONFIG", None)

    def tearDown(self):
        self.tmp.cleanup()
        for key, value in self._env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def _copy_data(self) -> Path:
        dest = self.base / "data"
        shutil.copytree(ROOT / "data", dest)
        return dest

    def test_registered_names_resolve(self):
        resolved = require_registered(
            [
                "pi_flywheel_canned",
                "pi_flywheel_sft_chat",
                "pi_flywheel_sft_alpaca",
                "pi_flywheel_preference",
                "pi_flywheel_eval_heldout",
            ],
            ROOT / "data",
        )
        self.assertTrue(resolved["pi_flywheel_canned"].is_file())
        cfg = load_path(ROOT / "configs" / "train" / "sft_canned.yaml")
        names = list(cfg["datasets"]) + [cfg["eval"]]
        self.assertTrue(set(names).issubset(resolved))

    def test_heldout_is_not_in_the_canned_map(self):
        table = json.loads((ROOT / "data" / "canned" / "canned_map.json").read_text(encoding="utf-8"))
        keys = {normalize_key(key) for key in table}
        for line in (ROOT / "data" / "seed" / "eval_heldout" / "eval_heldout.jsonl").read_text().splitlines():
            row = json.loads(line)
            self.assertNotIn(normalize_key(row["input"]), keys)

    def test_folded_map_keeps_the_first_answer(self):
        folded = _semantic_table({"hello.": "dotted", "hello": "plain"})
        self.assertEqual(folded, {"hello": "dotted"})
        again = _semantic_table({"Hello.": "first", "HELLO!": "second"})
        self.assertEqual(again, {"hello": "first"})
        self.assertEqual(normalize_key("hello."), "hello")

    def test_lookup_normalizes(self):
        os.environ["PI_PAIR_CANNED"] = str(ROOT / "data" / "canned" / "canned_map.json")
        try:
            self.assertEqual(lookup("Hi!"), "Hi. What can I help you with?")
            self.assertIsNone(lookup("this string is not in the canned map"))
        finally:
            os.environ.pop("PI_PAIR_CANNED", None)

    def test_weak_names_cannot_generate_even_if_flagged(self):
        self.assertFalse(may_generate({"name": "pi2", "generative": True, "role": "brain"}))
        self.assertFalse(may_generate({"name": "pi3", "generative": True, "role": "brain"}))
        self.assertTrue(may_generate({"name": "pi4", "generative": True, "role": "brain"}))
        with self.assertRaisesRegex(RuntimeError, "pi3 cannot be the brain"):
            chat_ollama(
                {"name": "pi3", "host": "127.0.0.1", "port": 1, "generative": True, "role": "brain"},
                "qwen2.5:0.5b",
                [{"role": "user", "content": "hi"}],
            )

    def test_queue_is_bounded_and_brain_does_not_write(self):
        data = self._copy_data()
        for index in range(QUEUE_BOUND + 5):
            append_row({"prompt": f"p{index}", "answer": "a"}, root=data, bound=QUEUE_BOUND)
        lines = (data / "train" / "pending" / "queue.jsonl").read_text().splitlines()
        self.assertEqual(len(lines), QUEUE_BOUND)
        self.assertIn("p5", lines[0])
        os.environ["PI_PAIR_ROLE"] = "brain"
        seen = []
        import pair.queue as queue

        original = queue.forward_row
        queue.forward_row = lambda row, opener=None, timeout=1.5: seen.append(row) or True
        try:
            note_exchange("secret prompt", "secret answer", chip="brain: pi4", peer="pi4", train=True)
        finally:
            queue.forward_row = original
        self.assertEqual(seen[0]["prompt"], "secret prompt")
        self.assertNotIn("secret prompt", (data / "train" / "pending" / "queue.jsonl").read_text())

    def test_post_train_grows_map_and_deletes_shards(self):
        data = self._copy_data()
        adapters = self.base / "adapters"
        before = json.loads((data / "canned" / "canned_map.json").read_text(encoding="utf-8"))
        queue = data / "train" / "pending" / "queue.jsonl"
        queue.parent.mkdir(parents=True, exist_ok=True)
        rows = [
            {"prompt": "zzz flywheel novel", "answer": "Folded from the miss queue."},
            {"prompt": "heya mesh", "answer": "should not enter the map"},
        ]
        queue.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
        result = post_train(root=data, adapters=adapters)
        self.assertEqual(result["added"], 1)
        self.assertGreaterEqual(result["rejected"], 1)
        self.assertEqual(result["rows_after"], result["rows_before"] + 1)
        after = json.loads((data / "canned" / "canned_map.json").read_text(encoding="utf-8"))
        self.assertEqual(after["zzz flywheel novel"], "Folded from the miss queue.")
        self.assertNotIn("heya mesh", after)
        self.assertEqual(len(before) + 1, len(after))
        self.assertFalse((data / "train" / "pending" / "queue.jsonl").exists())
        self.assertEqual(list((data / "train" / "active").glob("*.jsonl")), [])
        self.assertEqual(list((data / "prepared").glob("*.jsonl")), [])
        done = list((data / "train" / "done").glob("*.json"))
        self.assertEqual(len(done), 1)
        tomb = done[0].read_text(encoding="utf-8")
        self.assertNotIn("zzz flywheel novel", tomb)
        self.assertNotIn("Folded from the miss queue.", tomb)
        self.assertNotIn("heya mesh", tomb)
        manifest = json.loads((adapters / "active" / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["weights"], "pi4-ollama")
        self.assertEqual(manifest["added"], 1)
        self.assertFalse((adapters / "staging").exists())

    def test_unregistered_dataset_does_not_consume_the_queue(self):
        data = self._copy_data()
        queue = data / "train" / "pending" / "queue.jsonl"
        queue.parent.mkdir(parents=True, exist_ok=True)
        queue.write_text('{"prompt":"keep me","answer":"yes"}\n', encoding="utf-8")
        cfg = self.base / "bad.yaml"
        cfg.write_text(
            "id: bad\nstage: sft\nbase_model: qwen2.5:0.5b\ndatasets:\n  - not_a_dataset\neval: pi_flywheel_eval_heldout\n",
            encoding="utf-8",
        )
        with self.assertRaises(RegistryError):
            post_train(root=data, adapters=self.base / "adapters", config=cfg)
        self.assertIn("keep me", queue.read_text(encoding="utf-8"))

    def test_gate_failure_restores_the_queue(self):
        data = self._copy_data()
        map_path = data / "canned" / "canned_map.json"
        table = json.loads(map_path.read_text(encoding="utf-8"))
        table["heya mesh"] = "contaminated"
        map_path.write_text(json.dumps(table), encoding="utf-8")
        queue = data / "train" / "pending" / "queue.jsonl"
        queue.parent.mkdir(parents=True, exist_ok=True)
        queue.write_text('{"prompt":"still queued","answer":"yes"}\n', encoding="utf-8")
        with self.assertRaises(GateError):
            post_train(root=data, adapters=self.base / "adapters")
        self.assertIn("still queued", queue.read_text(encoding="utf-8"))
        self.assertEqual(list((data / "prepared").glob("*.jsonl")), [])
        self.assertFalse((self.base / "adapters" / "active" / "manifest.json").exists())

    def test_brain_role_refuses_the_job(self):
        os.environ["PI_PAIR_ROLE"] = "brain"
        with self.assertRaisesRegex(RuntimeError, "pi3"):
            post_train(root=self._copy_data(), adapters=self.base / "adapters")

    def test_labeled_row_is_queued_for_post_train(self):
        data = self._copy_data()
        adapters = self.base / "adapters"
        apply_label(
            "zzz labeled novel",
            "the bad reply",
            "down",
            "the corrected sentence",
            chip="brain: pi4",
            peer="pi4",
            root=data,
        )
        apply_label("zzz labeled reject", "do not keep", "down", root=data)
        apply_label("zzz labeled up", "keep this", "up", root=data)
        pending = data / "train" / "pending" / "queue.jsonl"
        rows = [json.loads(line) for line in pending.read_text(encoding="utf-8").splitlines()]
        labeled = rows[0]
        self.assertEqual(labeled["prompt"], "zzz labeled novel")
        self.assertEqual(labeled["answer"], "the bad reply")
        self.assertEqual(labeled["vote"], "down")
        self.assertEqual(labeled["correction"], "the corrected sentence")
        self.assertEqual(rows[1]["vote"], "down")
        self.assertNotIn("correction", rows[1])
        self.assertEqual(rows[2]["vote"], "up")
        active = data / "train" / "active" / "sample.jsonl"
        active.parent.mkdir(parents=True, exist_ok=True)
        active.write_text(pending.read_text(encoding="utf-8"), encoding="utf-8")
        _prepared, prepared_rows = _prepare(active, data, "sample")
        self.assertEqual(prepared_rows[0]["prompt"], "zzz labeled novel")
        self.assertEqual(prepared_rows[0]["answer"], "the bad reply")
        self.assertEqual(prepared_rows[0]["vote"], "down")
        self.assertEqual(prepared_rows[0]["correction"], "the corrected sentence")
        result = post_train(root=data, adapters=adapters)
        self.assertGreaterEqual(result["added"], 2)
        after = json.loads((data / "canned" / "canned_map.json").read_text(encoding="utf-8"))
        self.assertEqual(after["zzz labeled novel"], "the corrected sentence")
        self.assertEqual(after["zzz labeled up"], "keep this")
        self.assertNotIn("zzz labeled reject", after)
        self.assertNotIn("the bad reply", json.dumps(after))
        apply_label(
            "hi",
            after["hi"],
            "down",
            "Hello from the bench.",
            root=data,
        )
        post_train(root=data, adapters=adapters)
        replaced = json.loads((data / "canned" / "canned_map.json").read_text(encoding="utf-8"))
        self.assertEqual(replaced["hi"], "Hello from the bench.")
        done = list((data / "train" / "done").glob("*.json"))
        blob = "\n".join(path.read_text(encoding="utf-8") for path in done)
        self.assertNotIn("zzz labeled novel", blob)
        self.assertNotIn("the corrected sentence", blob)

    def test_high_water_drops_oldest_labels_and_keeps_the_map(self):
        os.environ["PI_PAIR_LABEL_HIGH_WATER_BYTES"] = "100000"
        os.environ["PI_PAIR_DISK_HIGH_WATER"] = "2"
        data = self._copy_data()
        map_before = (data / "canned" / "canned_map.json").read_text(encoding="utf-8")
        apply_label("oldest prompt aaa", "a" * 40, "down", root=data)
        apply_label("newest prompt bbb", "b" * 20, "up", "the corrected sentence", root=data)
        pending = data / "train" / "pending" / "queue.jsonl"
        total = pending.stat().st_size
        os.environ["PI_PAIR_LABEL_HIGH_WATER_BYTES"] = str(total - 1)
        apply_label("newest prompt bbb", "b" * 20, "up", "the corrected sentence", root=data)
        text = pending.read_text(encoding="utf-8")
        self.assertNotIn("oldest prompt aaa", text)
        self.assertIn("newest prompt bbb", text)
        self.assertIn('"vote": "up"', text)
        self.assertIn("the corrected sentence", text)
        self.assertEqual((data / "canned" / "canned_map.json").read_text(encoding="utf-8"), map_before)
        self.assertLess(pending.stat().st_size, total)

    def test_brain_forwards_a_label_and_does_not_write_it(self):
        data = self._copy_data()
        os.environ["PI_PAIR_ROLE"] = "brain"
        seen = []
        import pair.queue as queue

        original = queue.forward_feedback
        queue.forward_feedback = lambda payload, opener=None, timeout=1.5: seen.append(payload) or True
        try:
            result = apply_label("secret prompt", "secret answer", "up", root=data)
        finally:
            queue.forward_feedback = original
        self.assertTrue(result["forwarded"])
        self.assertEqual(seen[0]["prompt"], "secret prompt")
        self.assertEqual(seen[0]["vote"], "up")
        self.assertFalse((data / "train" / "pending" / "queue.jsonl").exists())

    def test_readme_covers_gates(self):
        text = (ROOT.parent / "README.md").read_text(encoding="utf-8")
        for phrase in (
            "armv7",
            "~1GB",
            "~8GB",
            "cannot be the brain",
            "pi4 unreachable on cache miss",
            "https://huggingface.co/datasets/akashnaren/pi-flywheel-canned",
            "https://huggingface.co/datasets/akashnaren/pi-flywheel-sft-seed",
            "https://huggingface.co/datasets/akashnaren/pi-flywheel-eval",
            "dataset_info.json",
            "canned_map.json",
            "data/train/pending",
            "OLLAMA_NUM_PARALLEL",
            "10.0.0.166",
            "18080",
            "pi-flywheel-improve",
            "rpi-pi4",
            "Axolotl",
            "LLaMA-Factory",
            "Unsloth",
            "nanoGPT",
            "CNN",
            "24 layers",
            "/v1/chat/completions",
            "/v1/flywheel/feedback",
            "vote",
            "correction",
            "does not make the model larger or smaller",
            "full fine-tune is not",
            "data/train/done",
            "map hit",
            "DuckDuckGo",
            "search failed",
            "The local model is small",
            "facts it does not know",
            "stock Qwen 2.5 0.5B",
            "The canned map is not a custom model",
            "only when a run updates weights",
            "which this board does not do",
            "pi2 does not decode",
            "POST /v1/search",
            "HF_TOKEN",
            "akashnaren/pi-mesh-labels",
            "KAGGLE_API_TOKEN",
            "short connect timeout",
            "scripts/chat_label.py",
            "--dry-run",
            "another caller",
        ):
            self.assertIn(phrase, text, phrase)
        self.assertNotIn("Pi 0.2 High", text)
        self.assertFalse((ROOT / "README.md").exists())
        peers = json.loads((ROOT / "peers.example.json").read_text(encoding="utf-8"))
        by_name = {peer["name"]: peer for peer in peers}
        self.assertFalse(by_name["pi2"]["generative"])
        self.assertFalse(by_name["pi3"]["generative"])
        self.assertTrue(by_name["pi4"]["generative"])
        self.assertEqual(by_name["pi2"]["role"], "health")
        self.assertEqual(by_name["pi3"]["role"], "dataset")
        self.assertEqual(by_name["pi4"]["role"], "brain")


if __name__ == "__main__":
    unittest.main()
