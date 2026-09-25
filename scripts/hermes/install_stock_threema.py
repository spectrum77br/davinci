#!/usr/bin/env python3
"""Install the stock watchdog and its dedicated Threema profile on the host.

The default is a read-only check. Use --apply explicitly after deploying this
repository. No messages are sent and no Hermes settings or schedules are changed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

BASELINE_SHA256 = "8f81b8801962448e06d528dbbf6cda480017daa3baf4c9d062abd177e4359ed4"
EXPECTED_ID = "*DVESTIM"
PROFILE_KEYS = ("THREEMA_ESTOQUE_GATEWAY_ID", "THREEMA_ESTOQUE_GATEWAY_SECRET")


class InstallError(Exception):
    """An operator-facing error that never includes credential values."""


def _dotenv_value(raw: str) -> str:
    raw = raw.strip()
    if not raw:
        return ""
    if raw[0] not in "\"'":
        return re.split(r"\s+#", raw, maxsplit=1)[0].strip()
    quote = raw[0]
    result: list[str] = []
    escapes = {
        "n": "\n",
        "r": "\r",
        "t": "\t",
        "b": "\b",
        "f": "\f",
        "v": "\v",
        "a": "\a",
    }
    i = 1
    while i < len(raw):
        character = raw[i]
        if character == quote:
            tail = raw[i + 1 :].strip()
            if tail and not tail.startswith("#"):
                raise InstallError(
                    "Invalid quoting in the stock Threema configuration."
                )
            return "".join(result)
        if character == "\\" and i + 1 < len(raw):
            following = raw[i + 1]
            if following in (quote, "\\"):
                result.append(following)
                i += 2
                continue
            if quote == '"' and following in escapes:
                result.append(escapes[following])
                i += 2
                continue
        result.append(character)
        i += 1
    raise InstallError("Unclosed quote in the stock Threema configuration.")


def read_stock_profile(path: Path) -> bytes:
    """Read only the two required keys; never expand variables or execute input."""
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        match = re.match(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=(.*)$", line)
        if not match or match[1] not in PROFILE_KEYS:
            continue
        key = match[1]
        if key in values:
            raise InstallError("Duplicate stock Threema configuration key.")
        values[key] = _dotenv_value(match[2])
    if not all(values.get(key, "").strip() for key in PROFILE_KEYS):
        raise InstallError("The stock Threema ID and secret must both be configured.")
    sender = values[PROFILE_KEYS[0]]
    if not re.fullmatch(r"\*[A-Z0-9]{7}", sender) or sender != EXPECTED_ID:
        raise InstallError(
            "The configured stock sender must be the approved ID *DVESTIM."
        )
    if any(
        ord(character) < 32 or ord(character) == 127
        for character in values[PROFILE_KEYS[1]]
    ):
        raise InstallError(
            "The stock Threema secret contains an invalid control character."
        )
    return (
        "".join(
            f"{key}={json.dumps(values[key], ensure_ascii=False)}\n"
            for key in PROFILE_KEYS
        )
    ).encode("utf-8")


@dataclass
class Change:
    path: Path
    content: bytes = field(repr=False)
    mode: int


def _current(path: Path) -> tuple[bytes, int] | None:
    if path.is_symlink():
        raise InstallError("Refusing to replace a symbolic-link destination.")
    if not path.exists():
        return None
    if not path.is_file():
        raise InstallError("An installation destination is not a regular file.")
    return path.read_bytes(), stat.S_IMODE(path.stat().st_mode)


def plan_install(args: argparse.Namespace) -> list[Change]:
    if not re.fullmatch(r"[0-9a-f]{64}", args.baseline_sha256):
        raise InstallError(
            "The baseline SHA256 must contain exactly 64 lowercase hex digits."
        )
    paths = (
        args.source.resolve(),
        args.env_file.resolve(),
        args.script.resolve(),
        args.profile.resolve(),
    )
    if len(set(paths)) != len(paths):
        raise InstallError(
            "Source, environment, script and profile must be different files."
        )
    source = args.source.read_bytes()
    if not source:
        raise InstallError("The watchdog source is empty.")
    existing_script = _current(args.script)
    if existing_script:
        current_hash = hashlib.sha256(existing_script[0]).hexdigest()
        if current_hash not in (
            args.baseline_sha256,
            hashlib.sha256(source).hexdigest(),
        ):
            raise InstallError(
                "The installed watchdog has changed outside this deployment; installation refused."
            )
    profile = read_stock_profile(args.env_file)
    # Credentials are installed before the watchdog so its first run has its profile.
    candidates = [
        Change(args.profile, profile, 0o600),
        Change(args.script, source, 0o644),
    ]
    return [
        change
        for change in candidates
        if _current(change.path) != (change.content, change.mode)
    ]


def _atomic_write(path: Path, content: bytes, mode: int) -> None:
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as output:
            os.fchmod(output.fileno(), mode)
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def apply_install(args: argparse.Namespace, changes: list[Change]) -> None:
    # Recheck immediately before any write, including credential rotations/drift.
    if plan_install(args) != changes:
        raise InstallError(
            "The installation inputs changed after checking; run the installer again."
        )
    if not changes:
        return
    if args.backup_dir.is_symlink():
        raise InstallError("Refusing to use a symbolic-link backup directory.")
    args.backup_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(args.backup_dir, 0o700)
    for change in changes:
        previous = _current(change.path)
        if previous is not None:
            descriptor, name = tempfile.mkstemp(
                prefix=f"{change.path.name}.", suffix=".bak", dir=args.backup_dir
            )
            with os.fdopen(descriptor, "wb") as backup:
                os.fchmod(backup.fileno(), 0o600)
                backup.write(previous[0])
                backup.flush()
                os.fsync(backup.fileno())
        change.path.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write(change.path, change.content, change.mode)


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    mode = result.add_mutually_exclusive_group()
    mode.add_argument(
        "--check", action="store_true", help="Check without writing (default)"
    )
    mode.add_argument(
        "--apply",
        action="store_true",
        help="Install the validated stock profile and watchdog",
    )
    result.add_argument("--env-file", type=Path, default=Path("/opt/davinci/.env"))
    result.add_argument(
        "--source",
        type=Path,
        default=Path(__file__).with_name("bling_negative_stock_watch.py"),
    )
    result.add_argument(
        "--script",
        type=Path,
        default=Path("/opt/hermes/data/scripts/bling_negative_stock_watch.py"),
    )
    result.add_argument(
        "--profile", type=Path, default=Path("/opt/hermes/data/threema-estoque.env")
    )
    result.add_argument(
        "--backup-dir",
        type=Path,
        default=Path("/opt/hermes/data/.threema-estoque-backups"),
    )
    result.add_argument("--baseline-sha256", default=BASELINE_SHA256)
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        changes = plan_install(args)
        if args.apply:
            apply_install(args, changes)
        action = "Applied" if args.apply else "Checked (no writes)"
        result = "changed" if args.apply else "require changes"
        print(
            f"{action}: stock sender {EXPECTED_ID}; {len(changes)} file(s) {result}."
        )
        return 0
    except InstallError as error:
        print(f"ERROR: {error}")
    except (OSError, UnicodeError):
        # OS/decoding errors can embed input bytes. Keep the operator error generic.
        print("ERROR: Could not safely read or write the installation files.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
