import json
from pathlib import Path


def test_inventory() -> None:
    inventory = json.loads(Path("inventory.json").read_text(encoding="utf-8"))
    assert inventory == {
        "by_path": {"src/a.py": 2, "src/c.py": 1, "src/d.py": 3, "src/f.py": 1},
        "total_matches": 7,
    }
    assert list(inventory) == sorted(inventory)
    assert list(inventory["by_path"]) == sorted(inventory["by_path"])
