def normalize_identifier(value: str) -> str:
    """Return a lowercase, dash-separated identifier."""

    return value.strip().lower().replace(" ", "-")
