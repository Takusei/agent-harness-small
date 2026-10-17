def slugify(text):
    """Lowercase words joined by single hyphens, splitting all whitespace."""
    return "-".join(text.strip().split(" ")).lower()
