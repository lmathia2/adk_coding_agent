from __future__ import annotations

from pathlib import Path

import harness.safety.approval as approval
import harness.tools.adk_adapter as adk_adapter


def test_package_facades_win_over_legacy_single_file_modules() -> None:
    assert Path(approval.__file__).name == "__init__.py"
    assert Path(adk_adapter.__file__).name == "__init__.py"
