"""Choose a peer. A pin never falls through to another Pi."""
from __future__ import annotations

from pair import health, runtime


def model_on_peer(models, model, kind) -> bool:
    if kind == "llamacpp":
        return bool(models)
    if not models:
        return True
    if model in models:
        return True
    base = model.split(":")[0]
    return any(base in (name or "") for name in models)


def healthy_peers(model):
    out = []
    for snap in health.snapshot_peers():
        if not snap["ok"]:
            continue
        kind = snap.get("kind") or "ollama"
        if not model_on_peer(snap.get("models") or [], model, kind):
            continue
        base = next(peer for peer in runtime.PEERS if peer["name"] == snap["name"])
        out.append({**base, **snap})
    return out


def pick(target, mesh_on, model):
    # Mesh off + pin = direct; mesh on + pin = still pin (no fallback)
    if not mesh_on or (target and target != "auto"):
        for peer in runtime.PEERS:
            if peer["name"] != target:
                continue
            ok, models, err, port = health.peer_health(peer)
            if not ok:
                note = peer.get("note") or ""
                raise RuntimeError(f"{target} offline" + (f" ({note})" if note else ""))
            kind = peer.get("kind") or "ollama"
            if not model_on_peer(models, model, kind):
                raise RuntimeError(f"{target} has no usable model")
            return dict(peer, ok=True, models=models, port=port, error=err)
        raise RuntimeError(f"unknown peer {target}")
    healthy = healthy_peers(model)
    if not healthy:
        raise RuntimeError("no peers up")
    return runtime.next_peer(healthy)
