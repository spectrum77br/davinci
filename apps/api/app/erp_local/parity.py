"""Pinned, reviewable functional contract, not a claim of complete server parity."""

import json
from pathlib import Path


def parity_status():
    return json.loads((Path(__file__).parent / "parity.json").read_text())
