"""dataset_info.json is the only way a train config may name a dataset."""
from __future__ import annotations

import json
from pathlib import Path

from pair.config import data_root


class RegistryError(RuntimeError):
    pass


def registry_file(root: Path | None = None) -> Path:
    base = root or data_root()
    for relative in (
        "dataset_info.json",
        "seed/dataset_info.json",
        "seed/schemas/dataset_info.json",
    ):
        candidate = base / relative
        if candidate.is_file():
            return candidate
    raise RegistryError(f"dataset_info.json missing under {base}")


def load_registry(root: Path | None = None) -> tuple[Path, dict]:
    path = registry_file(root)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RegistryError(f"unreadable registry {path}: {error}") from error
    if not isinstance(data, dict) or not data:
        raise RegistryError(f"{path} must be an object of dataset names")
    return path, data


def resolve_spec(registry_path: Path, spec: dict, key: str = "file_name") -> Path:
    name = spec.get(key)
    if not name or not isinstance(name, str):
        raise RegistryError(f"registry entry missing {key}")
    path = Path(name)
    if not path.is_absolute():
        path = registry_path.parent / path
    return path


def require_registered(names: list[str], root: Path | None = None) -> dict[str, Path]:
    path, registry = load_registry(root)
    resolved: dict[str, Path] = {}
    missing = [name for name in names if name not in registry]
    if missing:
        raise RegistryError(
            "unregistered datasets: " + ", ".join(missing) + f" (registry {path.name})"
        )
    for name in names:
        spec = registry[name]
        if not isinstance(spec, dict):
            raise RegistryError(f"{name} is not an object in {path}")
        file_path = resolve_spec(path, spec)
        if not file_path.is_file():
            raise RegistryError(f"{name} file missing: {file_path}")
        resolved[name] = file_path
    return resolved
