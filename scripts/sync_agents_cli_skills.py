#!/usr/bin/env python3
"""Synchronize selected Google Agents CLI skills at the pinned revision."""

from __future__ import annotations

import json
import shutil
import tempfile
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOCK_PATH = ROOT / ".agents" / "skills" / "upstream-lock.json"
DESTINATION = LOCK_PATH.parent


def main() -> int:
    lock = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
    repository = lock["repository"]
    revision = lock["revision"]
    selected = set(lock["skills"])
    archive_url = f"https://github.com/{repository}/archive/{revision}.zip"

    with tempfile.TemporaryDirectory(prefix="agents-cli-skills-") as temp_dir:
        temp = Path(temp_dir)
        archive = temp / "source.zip"
        urllib.request.urlretrieve(archive_url, archive)  # noqa: S310 - pinned HTTPS source
        with zipfile.ZipFile(archive) as source_zip:
            source_zip.extractall(temp)

        extracted_roots = [path for path in temp.iterdir() if path.is_dir()]
        if len(extracted_roots) != 1:
            raise RuntimeError("Unexpected agents-cli archive layout")

        skills_root = extracted_roots[0] / "skills"
        missing = selected - {path.name for path in skills_root.iterdir() if path.is_dir()}
        if missing:
            raise RuntimeError(f"Pinned archive is missing skills: {sorted(missing)}")

        for skill_name in sorted(selected):
            destination = DESTINATION / skill_name
            if destination.exists():
                shutil.rmtree(destination)
            shutil.copytree(skills_root / skill_name, destination)
            print(f"synced {skill_name}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
