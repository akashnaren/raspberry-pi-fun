#!/usr/bin/env python3
"""Validate Pi flywheel fixture data: JSON Schema subset, registry, decontamination.

Schemas are Draft 2020-12 documents. This checker enforces the keywords those
files actually use and rejects any other keyword, so a new constraint cannot
silently pass. Stdlib only.

Locks (manifest `locks`, fail closed):
  generate == ["pi4"]
  dataset_and_train == ["pi3"]
  health == ["pi2"]
  train_then_delete is true
  weak_gen is false

Eval `input` values must not match canned `input` values (case/whitespace folded).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

# Annotation keys are ignored. Everything else in a schema must be implemented.
_ANNOTATIONS = {"$schema", "$id", "title", "description"}
_IMPLEMENTED = _ANNOTATIONS | {
    "type",
    "additionalProperties",
    "required",
    "properties",
    "items",
    "minLength",
    "maxLength",
    "minItems",
    "maxItems",
    "minProperties",
    "enum",
}

# Mirrors pi-pair/data/dataset_info.json (LLaMA-Factory-style registry).
# `file` and `schema` are relative to the data root. SFT, preference, eval, and
# schemas live under seed/. Canned rows stay in canned/.
EXPECTED = {
    "pi_flywheel_canned": {
        "file": "canned/canned_seed.jsonl",
        "schema": "seed/schemas/canned.schema.json",
        "formatting": "canned_map",
        "stage": "serve",
        "columns": {"prompt": "input", "response": "answer"},
        "tags": ["canned", "fastpath", "seed"],
    },
    "pi_flywheel_sft_chat": {
        "file": "seed/sft_seed/sft_chat.jsonl",
        "schema": "seed/schemas/sft_chat.schema.json",
        "formatting": "sharegpt",
        "stage": "sft",
        "columns": {"messages": "messages"},
        "tags": ["sft", "chat", "seed"],
    },
    "pi_flywheel_sft_alpaca": {
        "file": "seed/sft_seed/sft_alpaca.jsonl",
        "schema": "seed/schemas/sft_alpaca.schema.json",
        "formatting": "alpaca",
        "stage": "sft",
        "columns": {"prompt": "instruction", "query": "input", "response": "output"},
        "tags": ["sft", "alpaca", "seed"],
    },
    "pi_flywheel_preference": {
        "file": "seed/sft_seed/preference_pairs.jsonl",
        "schema": "seed/schemas/preference.schema.json",
        "formatting": "preference",
        "stage": "dpo",
        "ranking": True,
        "columns": {"prompt": "prompt", "chosen": "chosen", "rejected": "rejected"},
        "tags": ["dpo", "preference", "seed"],
    },
    "pi_flywheel_eval_heldout": {
        "file": "seed/eval_heldout/eval_heldout.jsonl",
        "schema": "seed/schemas/eval_heldout.schema.json",
        "formatting": "eval_gate",
        "stage": "eval",
        "columns": {"prompt": "input"},
        "tags": ["eval", "heldout", "gate"],
    },
}

COUNT_KEYS = {
    "canned": "pi_flywheel_canned",
    "sft_chat": "pi_flywheel_sft_chat",
    "sft_alpaca": "pi_flywheel_sft_alpaca",
    "preference": "pi_flywheel_preference",
    "eval_heldout": "pi_flywheel_eval_heldout",
}

TEXT_FIELDS = {
    "pi_flywheel_canned": ("input", "answer"),
    "pi_flywheel_sft_alpaca": ("instruction", "input", "output"),
    "pi_flywheel_preference": ("prompt", "chosen", "rejected"),
    "pi_flywheel_eval_heldout": ("input", "answer"),
}


def _add(errors: list[dict], code: str, detail: str) -> None:
    errors.append({"code": code, "detail": detail})


def _type_ok(instance: object, expected: str) -> bool:
    if expected == "string":
        return isinstance(instance, str)
    if expected == "boolean":
        return isinstance(instance, bool)
    if expected == "integer":
        return isinstance(instance, int) and not isinstance(instance, bool)
    if expected == "number":
        return isinstance(instance, (int, float)) and not isinstance(instance, bool)
    if expected == "array":
        return isinstance(instance, list)
    if expected == "object":
        return isinstance(instance, dict)
    if expected == "null":
        return instance is None
    return False


def _unsupported(schema: dict, path: str, errors: list[dict]) -> None:
    for key, value in schema.items():
        if key not in _IMPLEMENTED:
            _add(errors, "schema_keyword", f"unsupported keyword {key} at {path}")
    props = schema.get("properties")
    if isinstance(props, dict):
        for name, child in props.items():
            if isinstance(child, dict):
                _unsupported(child, f"{path}.{name}", errors)
    items = schema.get("items")
    if isinstance(items, dict):
        _unsupported(items, f"{path}[]", errors)
    extra = schema.get("additionalProperties")
    if isinstance(extra, dict):
        _unsupported(extra, f"{path}.*", errors)


def validate_schema(instance: object, schema: dict, path: str = "$") -> list[dict]:
    """Return schema errors. Each item has path and message; callers map the code."""
    errors: list[dict] = []
    if not isinstance(schema, dict):
        return [{"path": path, "message": "schema is not an object"}]
    expected = schema.get("type")
    if expected is not None and not _type_ok(instance, expected):
        return [{"path": path, "message": f"expected {expected}"}]
    if "enum" in schema and instance not in schema["enum"]:
        errors.append({"path": path, "message": "value not in enum"})
    if isinstance(instance, str):
        if "minLength" in schema and len(instance) < schema["minLength"]:
            errors.append({"path": path, "message": f"shorter than {schema['minLength']}"})
        if "maxLength" in schema and len(instance) > schema["maxLength"]:
            errors.append({"path": path, "message": f"longer than {schema['maxLength']}"})
    if isinstance(instance, list):
        if "minItems" in schema and len(instance) < schema["minItems"]:
            errors.append({"path": path, "message": f"fewer than {schema['minItems']} items"})
        if "maxItems" in schema and len(instance) > schema["maxItems"]:
            errors.append({"path": path, "message": f"more than {schema['maxItems']} items"})
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for index, item in enumerate(instance):
                errors.extend(validate_schema(item, item_schema, f"{path}[{index}]"))
    if isinstance(instance, dict):
        if "minProperties" in schema and len(instance) < schema["minProperties"]:
            errors.append({"path": path, "message": f"fewer than {schema['minProperties']} properties"})
        required = schema.get("required") or []
        props = schema.get("properties") or {}
        for key in required:
            if key not in instance:
                errors.append({"path": f"{path}.{key}", "message": "required property missing"})
        additional = schema.get("additionalProperties", True)
        for key, value in instance.items():
            child_path = f"{path}.{key}"
            if key in props and isinstance(props[key], dict):
                errors.extend(validate_schema(value, props[key], child_path))
            elif additional is False:
                errors.append({"path": child_path, "message": "additional property"})
            elif isinstance(additional, dict):
                errors.extend(validate_schema(value, additional, child_path))
    return errors


def _load_json(path: Path, errors: list[dict]):
    if not path.is_file():
        _add(errors, "missing_path", str(path))
        return None
    raw = path.read_bytes()
    if b"\r" in raw:
        _add(errors, "crlf", str(path))
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        _add(errors, "invalid_json", f"{path}: {exc}")
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        _add(errors, "invalid_json", f"{path}: {exc}")
        return None


def _read_jsonl(path: Path, errors: list[dict]) -> list[tuple[int, dict]]:
    if not path.is_file():
        _add(errors, "missing_path", str(path))
        return []
    raw = path.read_bytes()
    if b"\r" in raw:
        _add(errors, "crlf", str(path))
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        _add(errors, "invalid_json", f"{path}: {exc}")
        return []
    if not text.strip():
        _add(errors, "empty_dataset", str(path))
        return []
    rows: list[tuple[int, dict]] = []
    for line_no, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            _add(errors, "blank_line", f"{path}:{line_no}")
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError as exc:
            _add(errors, "invalid_json", f"{path}:{line_no}: {exc}")
            continue
        if not isinstance(obj, dict):
            _add(errors, "invalid_json", f"{path}:{line_no}: row is not an object")
            continue
        rows.append((line_no, obj))
    if not rows:
        _add(errors, "empty_dataset", str(path))
    return rows


def _norm(text: str) -> str:
    return " ".join(text.strip().lower().split())


def _posix_rel(from_dir: Path, target: Path) -> str:
    return Path(os.path.relpath(target, from_dir)).as_posix()


def _check_registry(data_root: Path, errors: list[dict]) -> None:
    locations = (
        data_root / "dataset_info.json",
        data_root / "seed" / "dataset_info.json",
        data_root / "seed" / "schemas" / "dataset_info.json",
    )
    for path in locations:
        doc = _load_json(path, errors)
        if not isinstance(doc, dict):
            if doc is not None:
                _add(errors, "registry", f"{path} must be an object")
            continue
        if set(doc) != set(EXPECTED):
            _add(
                errors,
                "registry",
                f"{path} datasets {sorted(doc)} != {sorted(EXPECTED)}",
            )
        parent = path.parent.resolve()
        for name, spec in EXPECTED.items():
            entry = doc.get(name)
            if not isinstance(entry, dict):
                _add(errors, "registry", f"{path} missing object {name}")
                continue
            target = (data_root / spec["file"]).resolve()
            want_file = _posix_rel(parent, target)
            if entry.get("file_name") != want_file:
                _add(errors, "registry", f"{path} {name} file_name {entry.get('file_name')!r} != {want_file!r}")
            elif not target.is_file():
                _add(errors, "missing_path", str(target))
            if name == "pi_flywheel_canned":
                map_target = (data_root / "canned" / "canned_map.json").resolve()
                want_map = _posix_rel(parent, map_target)
                if entry.get("map_file") != want_map:
                    _add(
                        errors,
                        "registry",
                        f"{path} {name} map_file {entry.get('map_file')!r} != {want_map!r}",
                    )
            for key in ("formatting", "stage", "columns", "tags"):
                if entry.get(key) != spec[key]:
                    _add(errors, "registry", f"{path} {name} {key} {entry.get(key)!r} != {spec[key]!r}")
            if spec.get("ranking") is True:
                if entry.get("ranking") is not True:
                    _add(errors, "registry", f"{path} {name} ranking must be true")
            elif "ranking" in entry:
                _add(errors, "registry", f"{path} {name} must not set ranking")
            allowed = {"file_name", "formatting", "columns", "tags", "stage"}
            if spec.get("ranking") is True:
                allowed.add("ranking")
            if name == "pi_flywheel_canned":
                allowed.add("map_file")
            extra = set(entry) - allowed
            if extra:
                _add(errors, "registry", f"{path} {name} unexpected keys {sorted(extra)}")


def _check_locks(manifest: dict, errors: list[dict]) -> None:
    if manifest.get("synthetic") is not True:
        _add(errors, "not_synthetic", "BUILD_MANIFEST.synthetic must be true")
    if manifest.get("pii") is not False:
        _add(errors, "pii", "BUILD_MANIFEST.pii must be false")
    locks = manifest.get("locks")
    if not isinstance(locks, dict):
        _add(errors, "lock_missing", "BUILD_MANIFEST.locks object is required")
        return
    if locks.get("generate") != ["pi4"]:
        _add(errors, "lock_generate", f"generate must be ['pi4'], got {locks.get('generate')!r}")
    if locks.get("dataset_and_train") != ["pi3"]:
        _add(
            errors,
            "lock_dataset_and_train",
            f"dataset_and_train must be ['pi3'], got {locks.get('dataset_and_train')!r}",
        )
    if locks.get("health") != ["pi2"]:
        _add(errors, "lock_health", f"health must be ['pi2'], got {locks.get('health')!r}")
    if locks.get("train_then_delete") is not True:
        _add(errors, "lock_train_then_delete", "train_then_delete must be true")
    if locks.get("weak_gen") is not False:
        _add(errors, "lock_weak_gen", "weak_gen must be false")


def _check_rows(
    data_root: Path,
    errors: list[dict],
) -> tuple[dict[str, list[dict]], dict[str, str]]:
    rows_by_name: dict[str, list[dict]] = {}
    canned_inputs: dict[str, str] = {}
    for name, spec in EXPECTED.items():
        schema_path = data_root / spec["schema"]
        schema = _load_json(schema_path, errors)
        if isinstance(schema, dict):
            _unsupported(schema, spec["schema"], errors)
        elif schema is not None:
            _add(errors, "schema", f"{schema_path} must be an object")
            schema = None
        path = data_root / spec["file"]
        parsed = _read_jsonl(path, errors)
        rows = []
        seen_inputs: dict[str, int] = {}
        for line_no, row in parsed:
            rows.append(row)
            if isinstance(schema, dict):
                for problem in validate_schema(row, schema):
                    _add(errors, "schema", f"{path}:{line_no} {problem['path']}: {problem['message']}")
            for field in TEXT_FIELDS.get(name, ()):
                value = row.get(field)
                if isinstance(value, str) and not value.strip():
                    _add(errors, "blank_text", f"{path}:{line_no} {field} is blank")
            if name == "pi_flywheel_sft_chat":
                messages = row.get("messages")
                if isinstance(messages, list):
                    roles = [item.get("role") for item in messages if isinstance(item, dict)]
                    if "user" not in roles or "assistant" not in roles:
                        _add(errors, "sft_roles", f"{path}:{line_no} needs user and assistant")
                    for item in messages:
                        if isinstance(item, dict):
                            content = item.get("content")
                            if isinstance(content, str) and not content.strip():
                                _add(errors, "blank_text", f"{path}:{line_no} message content is blank")
            if name == "pi_flywheel_preference":
                chosen = row.get("chosen")
                rejected = row.get("rejected")
                if isinstance(chosen, str) and isinstance(rejected, str) and chosen.strip() == rejected.strip():
                    _add(errors, "preference_tie", f"{path}:{line_no} chosen equals rejected")
            if name == "pi_flywheel_canned":
                raw_input = row.get("input")
                if isinstance(raw_input, str):
                    folded = _norm(raw_input)
                    if folded in seen_inputs:
                        _add(
                            errors,
                            "duplicate_input",
                            f"{path}:{line_no} repeats input from line {seen_inputs[folded]}",
                        )
                    else:
                        seen_inputs[folded] = line_no
                        canned_inputs[folded] = raw_input
        rows_by_name[name] = rows
    return rows_by_name, canned_inputs


def _check_canned_map(data_root: Path, canned_rows: list[dict], errors: list[dict]) -> None:
    path = data_root / "canned" / "canned_map.json"
    schema_path = data_root / "seed" / "schemas" / "canned_map.schema.json"
    schema = None
    if schema_path.is_file():
        schema = _load_json(schema_path, errors)
        if isinstance(schema, dict):
            _unsupported(schema, "seed/schemas/canned_map.schema.json", errors)
        else:
            schema = None
    doc = _load_json(path, errors)
    if not isinstance(doc, dict):
        if doc is not None:
            _add(errors, "canned_map_mismatch", f"{path} must be an object")
        return
    if schema is not None:
        for problem in validate_schema(doc, schema):
            _add(errors, "schema", f"{path} {problem['path']}: {problem['message']}")
    expect = {}
    for row in canned_rows:
        raw_input = row.get("input")
        answer = row.get("answer")
        if isinstance(raw_input, str) and isinstance(answer, str):
            expect[raw_input] = answer
    if set(doc) != set(expect):
        missing = sorted(set(expect) - set(doc))
        extra = sorted(set(doc) - set(expect))
        _add(
            errors,
            "canned_map_mismatch",
            f"keys differ; missing={missing[:3]} extra={extra[:3]}",
        )
    for key, answer in expect.items():
        if key in doc and doc[key] != answer:
            _add(errors, "canned_map_mismatch", f"answer mismatch for {key!r}")


def _check_decontam(data_root: Path, eval_rows: list[dict], canned_inputs: dict[str, str], errors: list[dict]) -> None:
    path = data_root / EXPECTED["pi_flywheel_eval_heldout"]["file"]
    seen: dict[str, int] = {}
    for index, row in enumerate(eval_rows, 1):
        raw_input = row.get("input")
        if not isinstance(raw_input, str):
            continue
        folded = _norm(raw_input)
        if folded in seen:
            _add(errors, "duplicate_input", f"{path}:{index} repeats eval input from line {seen[folded]}")
        else:
            seen[folded] = index
        if folded in canned_inputs:
            _add(
                errors,
                "eval_overlaps_canned",
                f"{path}:{index} input matches canned input {canned_inputs[folded]!r}",
            )


def _check_counts(manifest: dict, rows_by_name: dict[str, list[dict]], errors: list[dict]) -> dict[str, int]:
    counts = manifest.get("counts")
    found = {key: len(rows_by_name.get(name, [])) for key, name in COUNT_KEYS.items()}
    if not isinstance(counts, dict):
        _add(errors, "count_mismatch", "BUILD_MANIFEST.counts object is required")
        return found
    for key, actual in found.items():
        if counts.get(key) != actual:
            _add(errors, "count_mismatch", f"counts.{key} is {counts.get(key)!r}, file has {actual}")
    return found


def validate(data_root: Path) -> dict:
    errors: list[dict] = []
    data_root = data_root.resolve()
    if not data_root.is_dir():
        _add(errors, "missing_path", str(data_root))
        return {"ok": False, "data_root": str(data_root), "counts": {}, "locks": None, "errors": errors}

    manifest_path = data_root / "BUILD_MANIFEST.json"
    manifest = _load_json(manifest_path, errors)
    if not isinstance(manifest, dict):
        manifest = {}
        if manifest_path.is_file():
            _add(errors, "invalid_json", f"{manifest_path} must be an object")
    else:
        _check_locks(manifest, errors)

    _check_registry(data_root, errors)
    rows_by_name, canned_inputs = _check_rows(data_root, errors)
    _check_canned_map(data_root, rows_by_name.get("pi_flywheel_canned", []), errors)
    _check_decontam(data_root, rows_by_name.get("pi_flywheel_eval_heldout", []), canned_inputs, errors)
    counts = _check_counts(manifest, rows_by_name, errors) if isinstance(manifest, dict) else {}

    errors.sort(key=lambda item: (item["code"], item["detail"]))
    locks = manifest.get("locks") if isinstance(manifest, dict) else None
    return {
        "ok": not errors,
        "data_root": str(data_root),
        "counts": counts,
        "locks": locks,
        "errors": errors,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate flywheel canned/SFT/eval fixtures.")
    parser.add_argument(
        "--data",
        type=Path,
        default=Path(__file__).resolve().parent.parent / "data",
        help="Data root (default: pi-pair/data). The broken_overlap fixture is a negative check.",
    )
    parser.add_argument("--report", type=Path, default=None, help="Write the JSON report here.")
    args = parser.parse_args(argv)
    try:
        report = validate(args.data)
    except Exception as exc:  # noqa: BLE001 — report the crash instead of a traceback-only CI log
        report = {
            "ok": False,
            "data_root": str(args.data),
            "counts": {},
            "locks": None,
            "errors": [{"code": "validator_crash", "detail": f"{exc.__class__.__name__}: {exc}"}],
        }
    text = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.report is not None:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)
    if report["errors"]:
        print(f"data stack invalid: {len(report['errors'])} error(s)", file=sys.stderr)
        for err in report["errors"]:
            print(f"  {err['code']}: {err['detail']}", file=sys.stderr)
        return 1
    print(f"data stack ok ({report['data_root']})", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
