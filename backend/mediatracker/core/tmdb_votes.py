"""Affichage API des notes TMDB (sans modifier la base)."""


def exposed_tmdb_vote_average(average: float | None, count: int | None) -> float | None:
    """Retourne None si la note est absente, nulle ou sans vote."""
    if average is None:
        return None
    if average == 0:
        return None
    if count == 0:
        return None
    return average