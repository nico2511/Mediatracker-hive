"""Règles de mot de passe pour la création de compte et le changement volontaire."""

from __future__ import annotations

import re

from mediatracker.api.errors import Problem

MIN_PASSWORD_LEN = 12

_COMMON_WORDS = frozenset(
    {
        "password",
        "motdepasse",
        "mot de passe",
        "changeme",
        "welcome",
        "letmein",
        "administrator",
        "admin",
        "login",
        "secret",
        "soleil",
        "bonjour",
        "azerty",
        "qwerty",
        "passw0rd",
        "master",
        "dragon",
        "monkey",
        "shadow",
        "sunshine",
        "princess",
        "football",
        "baseball",
        "trustno1",
        "superman",
        "batman",
        "access",
        "default",
        "jetaime",
        "iloveyou",
        "whatever",
        "killer",
        "charlie",
        "andrew",
        "joshua",
        "michael",
        "jennifer",
        "hunter",
        "mustang",
        "pokemon",
        "starwars",
        "merlin",
        "cookie",
        "matrix",
        "internet",
        "samsung",
        "windows",
    }
)

_SEQUENCE_ALPHABETS = (
    "0123456789",
    "abcdefghijklmnopqrstuvwxyz",
    "azertyuiopqsdfghjklmwxcvbn",
    "qwertyuiopasdfghjklzxcvbnm",
)


def _categories(password: str) -> set[str]:
    cats: set[str] = set()
    if any(c.islower() for c in password):
        cats.add("lower")
    if any(c.isupper() for c in password):
        cats.add("upper")
    if any(c.isdigit() for c in password):
        cats.add("digit")
    if any(not c.isalnum() for c in password):
        cats.add("symbol")
    return cats


def _trim_alpha_edges(password: str) -> str:
    start, end = 0, len(password)
    while start < end and not password[start].isalpha():
        start += 1
    while end > start and not password[end - 1].isalpha():
        end -= 1
    return password[start:end]


def _fragment_in_sequence(fragment: str) -> bool:
    if len(fragment) < 4:
        return False
    lower = fragment.lower()
    if not (lower.isalpha() or lower.isdigit()):
        return False
    for seq in _SEQUENCE_ALPHABETS:
        oriented = (seq, seq[::-1])
        for oriented_seq in oriented:
            if lower in oriented_seq:
                return True
    return False


def _has_predictable_sequence(password: str) -> bool:
    lower = password.lower()
    size = len(lower)
    for start in range(size):
        for end in range(start + 4, size + 1):
            if _fragment_in_sequence(lower[start:end]):
                return True
    return False


def _has_long_same_char_run(password: str) -> bool:
    if not password:
        return False
    run = 1
    for idx in range(1, len(password)):
        if password[idx] == password[idx - 1]:
            run += 1
            if run >= 4:
                return True
        else:
            run = 1
    return False


def _has_repeated_motif(password: str) -> bool:
    length = len(password)
    if length < 4:
        return False
    for motif_len in range(1, length // 2 + 1):
        if length % motif_len != 0:
            continue
        repeats = length // motif_len
        if repeats < 2:
            continue
        motif = password[:motif_len]
        if motif * repeats == password:
            return True
    return False


def _common_word_covers_half(password: str) -> bool:
    lower = password.lower()
    half = len(password) / 2
    trimmed = _trim_alpha_edges(password).lower()
    for word in _COMMON_WORDS:
        needle = word.lower()
        if len(needle) < 4:
            continue
        if needle in lower and len(needle) >= half:
            return True
        if trimmed and (trimmed == needle or (needle in trimmed and len(needle) >= len(trimmed) / 2)):
            return True
    return False


def _contains_username(password: str, username: str) -> bool:
    user = (username or "").strip().lower()
    if len(user) < 3:
        return False
    return user in password.lower()


def validate_new_password(password: str, *, username: str) -> None:
    """Lève Problem(422) avec un détail en français si le mot de passe est refusé."""
    if len(password) < MIN_PASSWORD_LEN:
        raise Problem(
            422,
            "Requête invalide",
            f"Le mot de passe doit contenir au moins {MIN_PASSWORD_LEN} caractères.",
        )
    if password.isdigit():
        raise Problem(422, "Requête invalide", "Le mot de passe ne peut pas être composé uniquement de chiffres.")
    if _has_long_same_char_run(password) or _has_repeated_motif(password):
        raise Problem(
            422,
            "Requête invalide",
            "Le mot de passe ne peut pas être un caractère ou un motif répété de façon excessive.",
        )
    if _has_predictable_sequence(password):
        raise Problem(
            422,
            "Requête invalide",
            "Le mot de passe est trop prévisible (suite de clavier, lettres ou chiffres).",
        )
    if _contains_username(password, username):
        raise Problem(422, "Requête invalide", "Le mot de passe ne doit pas contenir le nom d'utilisateur.")
    if _common_word_covers_half(password):
        raise Problem(422, "Requête invalide", "Ce mot de passe s'appuie sur un mot trop courant et est refusé.")
    if len(_categories(password)) < 2:
        raise Problem(
            422,
            "Requête invalide",
            "Le mot de passe doit mélanger au moins deux types parmi minuscules, majuscules, chiffres et symboles.",
        )
    if not re.search(r"[^\s]", password):
        raise Problem(422, "Requête invalide", "Le mot de passe est invalide.")