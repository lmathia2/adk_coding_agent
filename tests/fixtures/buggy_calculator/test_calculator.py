from calculator import multiply


def test_multiply() -> None:
    assert multiply(6, 7) == 42
    assert multiply(-3, 4) == -12
