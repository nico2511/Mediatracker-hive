"""Free-Telecharger search URLs. One search page by default."""

from urllib.parse import quote_plus


def search_url(base_url: str, query: str, year: int | None) -> str:
    text = quote_plus(query.strip())
    url = f"{base_url.rstrip('/')}/1/recherche1/1.html?rech_fiche={text}&rech_cat="
    if year is not None:
        url += f"&rech_annee={year}"
    return url


def search_phrases(title: str, original_title: str | None, year: int | None) -> list[tuple[str, int | None]]:
    """Title with year first, then without, then original title."""
    phrases: list[tuple[str, int | None]] = []
    for name in (title, original_title or ""):
        cleaned = " ".join(name.split())
        if not cleaned:
            continue
        if year is not None:
            key = (cleaned, year)
            if key not in phrases:
                phrases.append(key)
        key = (cleaned, None)
        if key not in phrases:
            phrases.append(key)
    return phrases
