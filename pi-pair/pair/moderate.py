"""The one moderation hook on the request path.

`safety_filter` in the inference config turns the local harmful-content filter
on. Off, every verdict is allow, and this process does not refuse or replace
a request. A later safety stack replaces `moderate`.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Verdict:
    """`allow` keeps the text. `refuse` replaces it with `replacement`."""

    action: str
    replacement: str = ""

    @property
    def refused(self) -> bool:
        return self.action == "refuse"


_ALLOW = Verdict("allow")


def safety_filter() -> bool:
    """Operator switch. False leaves every request on the normal answer path."""
    from pair.knobs import inference_knobs

    value = inference_knobs().get("safety_filter", False)
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def moderate(text: str) -> Verdict:
    """Allow or refuse one span.

    Replace this function to plug in another stack. Callers must use the
    verdict, not the lexicon, to decide a refusal or a replacement.
    """
    if not safety_filter():
        return _ALLOW
    from pair.assist import is_harmful, refusal_for

    sample = text or ""
    if is_harmful(sample):
        return Verdict("refuse", refusal_for(sample))
    return _ALLOW
