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

from pair.canned import lookup, normalize_key
from pair.chat import chat_ollama
from pair.guard import may_generate
from pair.lifecycle import GateError, post_train
from pair.queue import QUEUE_BOUND, append_row, note_exchange
from pair.registry import RegistryError, require_registered
from pair.yaml_lite import load_path


class Flywheel(unittest.TestCase):
    def setUp(self):
        self._env = {
            key: os.environ.get(key)
            for key in ("PI_PAIR_DATA", "PI_PAIR_ROLE", "PI_PAIR_ADAPTERS", "PI_PAIR_TRAIN_CONFIG", "PI_PAIR_NAME")
        }
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

    def test_lookup_normalizes(self):
        os.environ["PI_PAIR_CANNED"] = str(ROOT / "data" / "canned" / "canned_map.json")
        try:
            self.assertIn("Mesh assistant online", lookup("Hi!") or "")
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

    def test_readme_covers_gates(self):
        text = (ROOT / "README.md").read_text(encoding="utf-8")
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
        ):
            self.assertIn(phrase, text, phrase)
        self.assertNotIn("Pi 0.2 High", text)
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
