"""Phrase checks for the README and the docs that share its text."""

from __future__ import annotations

import unittest

from tests.support.paths import ROOT


class ProductCopy(unittest.TestCase):
    """The page title is OpenPi — MicroAstra. READMEs stay plain and do not say Pi PAIR."""

    def test_static_and_readmes_copy(self):
        product = "OpenPi — MicroAstra"
        retired = "Pi 0.2 High"
        files = [
            ROOT / "static" / "index.html",
            ROOT / "static" / "mesh.js",
            ROOT / "static" / "mesh.css",
            ROOT.parent / "README.md",
            ROOT / "install.sh",
            ROOT / "mesh-hello.sh",
            ROOT / "start.sh",
        ]
        problems = []
        for path in files:
            text = path.read_text(encoding="utf-8")
            rel = path.relative_to(ROOT.parent)
            if "Pi PAIR" in text or "PI PAIR" in text:
                problems.append(f"{rel} still says Pi PAIR")
        html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
        if "<title>OpenPi</title>" not in html:
            problems.append("static/index.html title is not OpenPi")
        if f"<title>{product}</title>" in html:
            problems.append("static/index.html tab title still includes MicroAstra")
        if f'id="brandName">{product}</span>' not in html:
            problems.append("static/index.html brand is not OpenPi — MicroAstra")
        install = (ROOT / "install.sh").read_text(encoding="utf-8")
        if "Description=Pi GPT 1.0\n" not in install:
            problems.append("install.sh Description is not Pi GPT 1.0")
        readme_images = (
            "rack-hero.jpg",
            "rack-front.jpg",
            "rack-top.jpg",
        )
        if (ROOT / "README.md").exists():
            problems.append("pi-pair/README.md competes with the repo root README")
        root_names = [
            path.name
            for path in ROOT.parent.iterdir()
            if path.is_file() and path.name.lower() == "readme.md"
        ]
        if root_names != ["README.md"]:
            problems.append(f"repo root README set is {root_names}")
        root_readme = (ROOT.parent / "README.md").read_text(encoding="utf-8")
        label = "README.md"
        text = root_readme
        readme_dir = ROOT.parent
        prefix = "pi-pair/docs/rack/"
        if not text.startswith("# pi-pair\n"):
            problems.append("README.md title is not pi-pair")
        if retired in text:
            problems.append(f"{label} still says {retired}")
        if "3D-printed server rack" not in text:
            problems.append(f"{label} does not mention the 3D-printed rack")
        hero = f"{prefix}rack-hero.jpg"
        if text.find(hero) == -1 or text.find(hero) > text.find(
            f"{prefix}rack-front.jpg"
        ):
            problems.append(f"{label} hero is not rack-hero.jpg")
        if "-render.jpg" in text:
            problems.append(f"{label} still links a CGI render")
        for name in readme_images:
            rel = f"{prefix}{name}"
            if f"]({rel})" not in text:
                problems.append(f"{label} missing image {rel}")
            if not (readme_dir / rel).is_file():
                problems.append(f"missing {rel}")
        for name in (
            "rack-hero-render.jpg",
            "rack-front-render.jpg",
            "rack-top-render.jpg",
            "rack-hero-readme.jpg",
            "rack-hero-studio.jpg",
            "rack-front-ports.jpg",
            "rack-front-ports-readme.jpg",
            "rack-front-ports-studio.jpg",
            "rack-top-readme.jpg",
            "rack-top-studio.jpg",
        ):
            if (ROOT / "docs" / "rack" / name).exists():
                problems.append(f"old photo still present: docs/rack/{name}")
        self.assertEqual(problems, [], "\n".join(problems))


def corpus() -> str:
    parts = [(ROOT.parent / "README.md").read_text(encoding="utf-8")]
    docs = ROOT / "docs"
    for path in sorted(docs.glob("*.md")):
        parts.append(path.read_text(encoding="utf-8"))
    return "\n".join(parts)


class DocPhrases(unittest.TestCase):
    def test_readme_phrases_live_in_the_docs_corpus(self):
        text = corpus()
        lowered = text.lower()
        for phrase in (
            "tesseract-ocr",
            "poppler-utils",
            "pdftoppm",
            "/v1/attachments",
            "4 MB",
            "4096",
            "qwen3:1.7b",
            "does not pull Pro",
            "ollama pull qwen3:1.7b",
            "X-Pi-Mode",
            "keep_alive",
            "MAX_LOADED_MODELS=2",
            "OLLAMA_MAX_LOADED_MODELS=2",
            "OLLAMA_KEEP_ALIVE=-1",
            "ollama-lan",
            "removes `snowflake-arctic-embed:m`",
            "PI_GPT_API_KEY",
            "/openapi.json",
            "/api/chat",
            "Swagger UI",
            "If `mode` is omitted, the model is Flash.",
            "Authorization: Bearer",
            "X-API-Key",
            "mode 600",
            "EnvironmentFile=",
            "Waiting for a free slot",
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
            "28 layers",
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
            "stock Qwen3 0.6B",
            "The canned map is not a custom model",
            "only when a run updates weights",
            "which this board does not do",
            "pi2 does not decode",
            "POST /v1/search",
            "HF_TOKEN",
            "akashnaren/pi-mesh-labels",
            "KAGGLE_API_TOKEN",
            "short connect timeout",
            "scripts/qa/chat_label.py",
            "--dry-run",
            "another caller",
        ):
            self.assertIn(phrase, text, phrase)
        self.assertIn("no cloud ocr api", lowered)
        # EMBED.md documents cosine similarity for the train fold. The README
        # still must not score chat that way.
        readme = (ROOT.parent / "README.md").read_text(encoding="utf-8")
        self.assertNotIn("cosine", readme.lower())
        for phrase in (
            "Loading Pro",
            "ollama pull snowflake-arctic-embed",
            "/api/embed",
            "Pi 0.2 High",
        ):
            self.assertNotIn(phrase, text, phrase)
        self.assertFalse((ROOT / "README.md").exists())
