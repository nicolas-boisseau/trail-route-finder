"""Preset zones: named groups of start points to try when the user's home area is flat.

Lookup order:
 1. $DATA_DIR/presets/<name>.json  (user-extensible)
 2. <plugin>/presets/<name>.json   (bundled with the skill)
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import _paths


@dataclass
class PresetStart:
    address: str
    label: str
    drive_min: int
    expected_dplus_max_15km: int  # rough ceiling for sanity-checking the user's D+ target


@dataclass
class Preset:
    name: str
    label: str
    starts: list[PresetStart]
    stretch_starts: list[PresetStart]


def _load_json(path: Path) -> Preset:
    data = json.loads(path.read_text(encoding="utf-8"))
    def parse(items): return [PresetStart(**i) for i in items]
    return Preset(
        name=data["name"],
        label=data["label"],
        starts=parse(data.get("starts", [])),
        stretch_starts=parse(data.get("stretch_starts", [])),
    )


def load(name: str) -> Preset:
    user_path = _paths.data_dir() / "presets" / f"{name}.json"
    if user_path.exists():
        return _load_json(user_path)
    bundled = _paths.plugin_root() / "presets" / f"{name}.json"
    if bundled.exists():
        return _load_json(bundled)
    raise FileNotFoundError(
        f"Unknown preset {name!r}. Looked in {user_path} and {bundled}. "
        f"Available bundled: {[p.stem for p in (_paths.plugin_root() / 'presets').glob('*.json')]}"
    )


def list_available() -> list[str]:
    names = set()
    user_dir = _paths.data_dir() / "presets"
    bundled_dir = _paths.plugin_root() / "presets"
    for d in (user_dir, bundled_dir):
        if d.exists():
            names.update(p.stem for p in d.glob("*.json"))
    return sorted(names)
