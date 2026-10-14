"""Headless LibreOffice conversions that cannot hang or fill the disk.

The profile, scratch files, and converted output share one temporary
directory. The wait is at most CONVERT_TIMEOUT_S seconds. A watchdog kills
the process group if that directory grows past MAX_TEMP_BYTES. The finally
block always signals the group, even when the wrapper has already exited,
and then deletes the directory.
"""

from __future__ import annotations

import errno
import os
import resource
import shutil
import signal
import subprocess
import tempfile
import time
from pathlib import Path

CONVERT_TIMEOUT_S = 60
MAX_OUTPUT_BYTES = 8 * 1024 * 1024
MAX_TEMP_BYTES = 256 * 1024 * 1024
# One scratch file inside the profile can grow while soffice is stuck.
_MAX_FILE_BYTES = 64 * 1024 * 1024
_POLL_S = 0.1


class OfficeError(Exception):
    """The conversion did not return a file within the limit."""


def soffice_bin() -> str | None:
    return shutil.which("soffice") or shutil.which("libreoffice")


def convert_office(
    data: bytes,
    source_ext: str,
    target: str,
    *,
    timeout: float = CONVERT_TIMEOUT_S,
    command: list[str] | None = None,
) -> bytes:
    """Convert ``data`` from ``source_ext`` into ``target`` bytes.

    ``command`` replaces the LibreOffice binary. Tests pass a fake command
    that sleeps or writes a stand-in file. The timeout cannot exceed
    CONVERT_TIMEOUT_S.
    """
    limit = min(float(timeout), float(CONVERT_TIMEOUT_S))
    if limit <= 0:
        limit = float(CONVERT_TIMEOUT_S)
    root = Path(tempfile.mkdtemp(prefix="office-convert-"))
    proc: subprocess.Popen[bytes] | None = None
    try:
        profile, outdir, source = _prepare(root, data, source_ext)
        proc = _spawn(_argv(source, profile, outdir, target, command), root)
        _stdout, stderr = _wait(proc, root, limit)
        if proc.returncode != 0:
            raise OfficeError(_tail(stderr))
        return _read_output(outdir, target)
    finally:
        if proc is not None:
            _kill_group(proc)
        shutil.rmtree(root, ignore_errors=True)


def _prepare(root: Path, data: bytes, source_ext: str) -> tuple[Path, Path, Path]:
    profile = root / "profile"
    outdir = root / "out"
    for path in (profile, outdir, root / "tmp", root / "cache", root / "config"):
        path.mkdir()
    source = root / f"source.{_plain_ext(source_ext, 'bad source extension')}"
    source.write_bytes(data)
    return profile, outdir, source


def _plain_ext(value: str, message: str) -> str:
    ext = (value or "").split(":", 1)[0].lstrip(".").lower()
    if not ext.isascii() or not ext.isalnum():
        raise OfficeError(message)
    return ext


def _argv(
    source: Path,
    profile: Path,
    outdir: Path,
    target: str,
    command: list[str] | None,
) -> list[str]:
    _plain_ext(target, "bad target")
    binary = list(command) if command else [soffice_bin() or "soffice"]
    argv = [
        *binary,
        "--headless",
        "--norestore",
        "--nolockcheck",
        "--nologo",
        "--nofirststartwizard",
        f"-env:UserInstallation={profile.as_uri()}",
        "--convert-to",
        target,
        "--outdir",
        str(outdir),
        str(source),
    ]
    return argv


def _limit_fsize() -> None:
    """Cap each file this process and its children create."""
    ceiling = _MAX_FILE_BYTES
    _soft, hard = resource.getrlimit(resource.RLIMIT_FSIZE)
    if hard == resource.RLIM_INFINITY or hard > ceiling:
        resource.setrlimit(resource.RLIMIT_FSIZE, (ceiling, ceiling))
        return
    resource.setrlimit(resource.RLIMIT_FSIZE, (hard, hard))


def _spawn(argv: list[str], root: Path) -> subprocess.Popen[bytes]:
    tmp = str(root / "tmp")
    env = os.environ.copy()
    env["HOME"] = str(root)
    env["TMPDIR"] = tmp
    env["TEMP"] = tmp
    env["TMP"] = tmp
    env["XDG_CACHE_HOME"] = str(root / "cache")
    env["XDG_CONFIG_HOME"] = str(root / "config")
    env["SAL_USE_VCLPLUGIN"] = "svp"
    try:
        return subprocess.Popen(
            argv,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=root,
            env=env,
            start_new_session=True,
            preexec_fn=_limit_fsize,
        )
    except FileNotFoundError as exc:
        name = argv[0] if argv else "soffice"
        raise OfficeError(f"{name} is not installed") from exc


def _read_output(outdir: Path, target: str) -> bytes:
    ext = _plain_ext(target, "bad target")
    found = sorted(path for path in outdir.glob(f"*.{ext}") if path.is_file())
    if len(found) != 1:
        raise OfficeError("no output")
    with found[0].open("rb") as handle:
        blob = handle.read(MAX_OUTPUT_BYTES + 1)
    if len(blob) > MAX_OUTPUT_BYTES:
        raise OfficeError("output too large")
    return blob


def _tail(stderr: bytes | None) -> str:
    lines = (stderr or b"").decode("utf-8", "replace").strip().splitlines()
    return lines[-1][:180] if lines else "conversion failed"


def _wait(
    proc: subprocess.Popen[bytes], root: Path, timeout: float
) -> tuple[bytes, bytes]:
    """Wait out the timeout, and stop early when the temp dir grows too far."""
    deadline = time.monotonic() + timeout
    while True:
        if _tree_size(root) > MAX_TEMP_BYTES:
            raise OfficeError("temp dir too large")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise OfficeError("timed out")
        try:
            stdout, stderr = proc.communicate(timeout=min(_POLL_S, remaining))
        except subprocess.TimeoutExpired:
            continue
        if _tree_size(root) > MAX_TEMP_BYTES:
            raise OfficeError("temp dir too large")
        return stdout or b"", stderr or b""


def _tree_size(root: Path) -> int:
    total = 0
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames[:] = [
            name for name in dirnames if not os.path.islink(os.path.join(dirpath, name))
        ]
        for name in filenames:
            path = os.path.join(dirpath, name)
            if os.path.islink(path):
                continue
            try:
                total += os.path.getsize(path)
            except OSError:
                continue
    return total


def _kill_group(proc: subprocess.Popen[bytes]) -> None:
    """SIGKILL the session, even if its leader has already exited, then reap."""
    if proc.pid:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except OSError as exc:
            if exc.errno != errno.ESRCH:
                try:
                    proc.kill()
                except OSError:
                    pass
    try:
        proc.wait(timeout=2)
    except subprocess.TimeoutExpired:
        try:
            proc.kill()
        except OSError:
            pass
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            pass
    for stream in (proc.stdout, proc.stderr, proc.stdin):
        if stream is None:
            continue
        try:
            stream.close()
        except OSError:
            pass
