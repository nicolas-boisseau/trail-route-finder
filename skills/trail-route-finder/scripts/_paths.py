"""Resolve where mutable data lives.

Code is bundled with the plugin (read-only after install).
Tiles, venv, generated GPX/HTML are mutable — they live in DATA_DIR.

Priority:
 1. $TRAIL_ROUTE_FINDER_DATA
 2. $CLAUDE_PLUGIN_DATA   (set by Claude Code when running from a plugin install)
 3. ~/.local/share/trail-route-finder
"""
from __future__ import annotations

import os
from pathlib import Path


def data_dir() -> Path:
    for env in ("TRAIL_ROUTE_FINDER_DATA", "CLAUDE_PLUGIN_DATA"):
        v = os.environ.get(env)
        if v:
            return Path(v).expanduser()
    return Path.home() / ".local" / "share" / "trail-route-finder"


def output_dir() -> Path:
    p = data_dir() / "output"
    p.mkdir(parents=True, exist_ok=True)
    return p


def plugin_root() -> Path:
    """The skill's bundled root (where SKILL.md, scripts/, profiles/, docker/ live)."""
    return Path(__file__).resolve().parent.parent
