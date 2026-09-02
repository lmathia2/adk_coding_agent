from identifiers import normalize_identifier


def test_normalize_identifier_collapses_separator_runs() -> None:
    assert normalize_identifier("  Alpha__ beta---GAMMA  ") == "alpha-beta-gamma"


def test_normalize_identifier_keeps_unicode_letters() -> None:
    assert normalize_identifier("Crème brûlée") == "crème-brûlée"
