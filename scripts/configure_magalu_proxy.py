#!/usr/bin/env python3
"""Apply only MAGALU_PROXY_URL to the production .env; credentials arrive on stdin."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
from pathlib import Path
from urllib.parse import quote


def candidate(content: bytes, credentials: dict) -> bytes:
    username = credentials.get("username")
    password = credentials.get("password")
    if username != "davinci-magalu" or not isinstance(password, str):
        raise ValueError("Invalid proxy credentials")
    if not re.fullmatch(r"[A-Za-z0-9_-]{32,256}", password):
        raise ValueError("Invalid proxy password format")
    value = "http://" + username + ":" + quote(password, safe="") + "@magalu_proxy:13129"
    lines = content.decode("utf-8").splitlines(keepends=True)
    indices = [
        i
        for i, line in enumerate(lines)
        if re.match(r"^\s*(?:export\s+)?MAGALU_PROXY_URL\s*=", line)
    ]
    if len(indices) > 1:
        raise ValueError("Duplicate MAGALU_PROXY_URL entries")
    replacement = "MAGALU_PROXY_URL=" + value + "\n"
    if indices:
        lines[indices[0]] = replacement
    else:
        if lines and not lines[-1].endswith("\n"):
            lines[-1] += "\n"
        lines.append(replacement)
    return "".join(lines).encode("utf-8")


def configure(path: Path, credentials: dict, *, apply: bool = False) -> bool:
    if path.is_symlink() or not path.is_file():
        raise ValueError("Expected a regular environment file")
    before = path.read_bytes()
    after = candidate(before, credentials)
    if before == after:
        return False
    if not apply:
        return True
    descriptor, temporary = tempfile.mkstemp(prefix=".magalu-env-candidate-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as output:
            os.fchmod(output.fileno(), 0o600)
            output.write(after)
            output.flush()
            os.fsync(output.fileno())
        if path.read_bytes() != before:
            raise ValueError("Environment changed during preparation; retry")
        descriptor, backup = tempfile.mkstemp(prefix=path.name + ".magalu-backup-", dir=path.parent)
        with os.fdopen(descriptor, "wb") as output:
            os.fchmod(output.fileno(), 0o600)
            output.write(before)
            output.flush()
            os.fsync(output.fileno())
        if path.read_bytes() != before:
            raise ValueError("Environment changed before replacement; retry")
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true")
    mode.add_argument("--apply", action="store_true")
    parser.add_argument("--env-file", type=Path, default=Path("/opt/davinci/.env"))
    args = parser.parse_args()
    try:
        credentials = json.load(sys.stdin)
        if not isinstance(credentials, dict):
            raise ValueError("Expected a credential object")
        changed = configure(args.env_file, credentials, apply=args.apply)
    except (OSError, ValueError, UnicodeError):
        print("Could not safely configure the Magalu proxy; no credentials are printed.")
        return 1
    state = "applied" if args.apply else "checked without writing"
    print(
        f"Magalu proxy {state}; configuration {'changed/pending' if changed else 'already current'}."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
