"""Queue timings and the French error sentences shared by the pipeline."""

import logging
import re
from datetime import timedelta

from mediatracker.providers.downloaders.aria2 import scrub_url

logger = logging.getLogger("mediatracker.core.pipeline")


BACKOFFS = (
    timedelta(minutes=5),
    timedelta(minutes=30),
    timedelta(hours=2),
    timedelta(hours=12),
)
# A discontinued magnet must not be uploaded again on the next 2 s tick.
COMET_BACKOFFS = (
    timedelta(minutes=10),
    timedelta(minutes=30),
    timedelta(hours=2),
    timedelta(hours=12),
)
COMET_PER_CYCLE = 3
# aria2 502 / error 29: retry, then ask AllDebrid for a fresh link, then the next hoster.
ARIA_CDN_REUNLOCK = 2
ARIA_CDN_BURY = 3
MAX_ATTEMPTS = 3
# An active job that never leaves 0 bytes is handed to the next hoster.
ZERO_PROGRESS_LIMIT = timedelta(minutes=30)
_STALLED_MINUTES = int(ZERO_PROGRESS_LIMIT.total_seconds() // 60)
ACTIVE = ("resolving", "queued", "downloading", "post_processing")
TERMINAL = ("done", "failed", "cancelled")
# A finished episode makes these leftover rows misleading. Active transfers stay put.
_STALE_WHEN_OBTAINED = frozenset({"failed", "cancelled", "pending", "resolving"})
VIDEO_EXT = frozenset({".mkv", ".mp4", ".avi", ".m4v", ".ts", ".webm", ".mov"})
REDISCOVER = "REDISCOVER"
# REDISCOVER row whose remote-search slot was taken in redownload_item (do not skip begin_remote_search).
REDISCOVER_RESERVED = "REDISCOVER_RESERVED"
REDISCOVER_MESSAGE = "Nouvelle recherche planifiée."
# AllDebrid marks this retryable. The pipeline still switches hoster at once:
# waiting on the same Nitroflare link is what left jobs at 0 % overnight.
_HOST_DOWN = frozenset({"LINK_HOST_UNAVAILABLE"})
# A dead file or a redirector that found nothing: try the next hoster now.
_DEAD_LINK = frozenset(
    {
        "LINK_DOWN",
        "REDIRECTOR_ERROR",
        "REDIRECTOR_NOT_SUPPORTED",
        "LINK_HOST_NOT_SUPPORTED",
        "LINK_EMPTY",
        "LINK_NOT_VIDEO",
    }
)
# AllDebrid will not accept this magnet again. Trying it on the next tick only loops.
_PERMANENT_MAGNET = frozenset({"DISCONTINUED"})
_BTIH = re.compile(r"btih:([a-zA-Z0-9]+)", re.IGNORECASE)
_MAGNET_SOURCE_HASH = re.compile(r"(?:comet|torrentio):([a-zA-Z0-9]+)", re.IGNORECASE)
_RELEASE_HINT = re.compile(r"(?i)(?:1080p|720p|2160p|480p|webrip|web-dl|bluray|multi)")
_HTML_TYPES = ("text/html", "application/xhtml", "text/plain", "application/json", "text/xml")
_HTML_MAGIC = (b"<!doctype", b"<html", b"<?xml", b"<head", b"<body")
_FRENCH = {
    "LINK_DOWN": "Lien hors ligne.",
    "REDIRECTOR_ERROR": "Le redirecteur n'a pas pu extraire les liens.",
    "REDIRECTOR_NOT_SUPPORTED": "AllDebrid ne prend pas en charge ce redirecteur (REDIRECTOR_NOT_SUPPORTED).",
    "LINK_HOST_NOT_SUPPORTED": "Hébergeur non pris en charge.",
    "LINK_EMPTY": "Le redirecteur n'a renvoyé aucun lien.",
    "LINK_NOT_VIDEO": "Le lien débridé n'est pas une vidéo (page d'erreur ou fichier trop petit).",
    "NO_LINK": "Aucun hébergeur utilisable.",
    "LINK_HOST_UNAVAILABLE": "Hébergeur indisponible.",
    "STALLED": f"Aucun progrès depuis {_STALLED_MINUTES} minutes.",
    "DISCONTINUED": "Cet aimant a été retiré par AllDebrid (DISCONTINUED).",
    "MAGNET_TIMEOUT": "AllDebrid n'a pas préparé l'aimant à temps.",
    "SIZE_SANITY": "Tous les liens ont une taille incompatible avec la durée attendue.",
    "ARIA2_CDN": "Le lien débridé a échoué (erreur aria2 502). Un autre hébergeur sera essayé.",
}


def french_error(code: str | None, message: str | None) -> str:
    """One French sentence. Repeated AllDebrid text is collapsed."""
    if code and code in _FRENCH:
        return _FRENCH[code]
    text = scrub_url(message or "").strip()
    if code and text.lower().startswith(code.lower()):
        text = text[len(code) :].lstrip(": -").strip()
    lowered = text.lower()
    if "could not extract links" in lowered:
        return _FRENCH["REDIRECTOR_ERROR"]
    if host_down(code, text):
        return _FRENCH["LINK_HOST_UNAVAILABLE"]
    if lowered in {"hors ligne", "this link is not available", "lien mort"}:
        return _FRENCH["LINK_DOWN"]
    half = len(text) // 2
    if half and text[:half].strip(" .") == text[half:].strip(" ."):
        text = text[:half].strip(" .")
    return text or (code or "Échec du téléchargement.")


def host_down(code: str | None, message: str | None) -> bool:
    """AllDebrid « host under maintenance or not available », whatever the wording."""
    if code in _HOST_DOWN:
        return True
    text = (message or "").casefold()
    if "under maintenance" in text:
        return True
    return "host unavailable" in text or "hébergeur indisponible" in text

