from textkit import slugify


def test_slugify_normalizes_words_and_punctuation() -> None:
    assert slugify("  Hello, Skein!  ") == "hello-skein"
    assert slugify("already---split") == "already-split"
