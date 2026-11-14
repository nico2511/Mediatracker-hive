"""French notification title and message shared by ntfy and Home Assistant."""

from typing import Any


def event_label(event_type: str, payload: dict[str, Any] | None = None) -> str:
    """Short French title for the journal and search."""
    title, _message = notification_text(event_type, payload or {})
    return title


def notification_text(event_type: str, payload: dict[str, Any]) -> tuple[str, str]:
    if event_type == "nouveautes":
        return _nouveautes(payload)
    if payload.get("grouped"):
        return _grouped(event_type, payload)
    show = str(payload.get("title") or "MediaTracker")
    code = _code(payload)
    if event_type == "source.checked":
        detail = str(payload.get("message") or payload.get("result") or "contrôle terminé")
        return "Vérification", f"{show} — {detail}"
    if event_type == "source.domain_proposed":
        host = payload.get("host") or "nouveau domaine"
        return "Domaine proposé", f"Wawacity semble avoir migré vers {host}."
    if event_type == "source.domain_applied":
        host = payload.get("host") or "nouveau domaine"
        return "Domaine appliqué", f"L'URL Wawacity pointe maintenant vers {host}."
    if event_type == "source.domain_auto_migrated":
        host = payload.get("host") or "nouveau domaine"
        previous = payload.get("previous_host")
        reason = str(payload.get("reason") or "redirection détectée")
        if previous:
            return (
                "Domaine Wawacity mis à jour",
                f"Migration automatique de {previous} vers {host} ({reason}).",
            )
        return "Domaine Wawacity mis à jour", f"Migration automatique vers {host} ({reason})."
    if event_type == "source.domain_reverted":
        host = payload.get("host") or "domaine précédent"
        return "Domaine rétabli", f"L'URL Wawacity a été remise sur {host}."
    if event_type == "source.redirect_blocked":
        host = payload.get("host") or "cible"
        return (
            "Redirection refusée",
            f"Wawacity a tenté une redirection vers {host}, refusée par la politique de sécurité.",
        )
    if event_type == "library.imported":
        if code:
            return "Nouvel épisode", f"{show} — {code} est disponible sur le NAS."
        if payload.get("media_type") == "film":
            return "Film disponible", f"{show} est disponible sur le NAS."
        return "Disponible", f"{show} est disponible sur le NAS."
    if event_type == "item.available":
        if code:
            return "Nouvel épisode", f"{show} — {code} est disponible et n'a pas été téléchargé."
        if payload.get("media_type") == "film":
            return "Film disponible", f"{show} est disponible et n'a pas été téléchargé."
        return "Disponible", f"{show} est disponible et n'a pas été téléchargé."
    if event_type == "download.redownload":
        detail = str(payload.get("message") or "Retéléchargement demandé.")
        return "Retéléchargement", f"{show} : {detail}"
    if event_type == "download.failed":
        detail = str(payload.get("error") or "échec définitif")
        subject = f"{show} — {code}" if code else show
        return "Téléchargement échoué", f"{subject} : {detail}"
    if event_type == "debrid.auth_blocked":
        detail = str(payload.get("message") or "Confirmation e-mail requise.")
        return "AllDebrid bloqué", detail
    if event_type == "aria2.unreachable":
        detail = str(payload.get("message") or "aria2 est injoignable.")
        return "aria2 injoignable", detail
    if event_type == "aria2.reachable":
        detail = str(payload.get("message") or "aria2 répond à nouveau.")
        return "aria2 rétabli", detail
    if event_type == "source.error":
        return "Source indisponible", f"{show} : la source ne répond plus (domaine ou Cloudflare)."
    if event_type == "source.outage":
        detail = str(payload.get("message") or "Service en panne.")
        return "Source en panne", f"{show} : {detail}"
    if event_type == "source.recovered":
        detail = str(payload.get("message") or "Service rétabli.")
        return "Source rétablie", f"{show} : {detail}"
    if event_type == "season.announced":
        season = payload.get("season")
        label = f"la saison {season}" if isinstance(season, int) else "une nouvelle saison"
        return "Nouvelle saison", f"{show} — {label} est annoncée sur TMDB."
    if event_type == "cleanup.upcoming":
        hours = payload.get("hours") or 24
        if isinstance(payload.get("season"), int):
            return (
                "Nettoyage à venir",
                f"{show} — saison {payload['season']} sera mise à la corbeille dans environ {hours} h.",
            )
        return "Nettoyage à venir", f"{show} sera mis à la corbeille dans environ {hours} h."
    if event_type == "cleanup.pass":
        detail = str(payload.get("message") or "Passage du nettoyage terminé.")
        return "Passage du nettoyage", detail
    if event_type == "cleanup.failed":
        detail = str(payload.get("message") or "Déplacement vers la corbeille impossible.")
        return "Nettoyage impossible", f"{show} : {detail}"
    if event_type in {"trash.emptied", "trash.purged"}:
        detail = str(payload.get("message") or "Corbeille mise à jour.")
        title = "Corbeille vidée" if event_type == "trash.emptied" else "Fichier purgé"
        return title, detail
    if event_type == "cleanup.missing":
        detail = str(payload.get("message") or "Fichier déjà absent du disque.")
        return "Fichier absent", f"{show} : {detail}"
    if event_type == "cleanup.done":
        return _cleanup_done(payload, show, code)
    if event_type == "disk.low":
        return (
            "Espace disque",
            "Le volume des téléchargements est sous le seuil. Les téléchargements sont en pause.",
        )
    if event_type == "library.file_suspect":
        detail = str(payload.get("message") or "Fichier suspect.")
        return "Fichier suspect", f"{show} : {detail}"
    if event_type == "library.scan_suspended":
        reason = str(payload.get("reason") or "")
        if reason == "empty_root":
            detail = "La racine de la bibliothèque semble vide ou inaccessible."
        elif reason == "mass_absence":
            detail = "Trop de fichiers connus sont absents d'un coup."
        else:
            detail = "Le scan bibliothèque est suspendu."
        return "Bibliothèque", detail
    if event_type == "update.available":
        remote = payload.get("remote_sha")
        short = str(remote)[:12] if isinstance(remote, str) and remote else ""
        extra = f" ({short})" if short else ""
        return "Mise à jour", f"Une nouvelle image MediaTracker est disponible{extra}."
    if event_type == "system.updated":
        new_version = str(payload.get("new_version") or "")
        new_sha = payload.get("new_sha")
        extra = ""
        if isinstance(new_sha, str) and new_sha:
            extra = f" (image {new_sha[:12]})"
        else:
            built = payload.get("new_build_date")
            if isinstance(built, str) and built.strip():
                extra = f" (construite le {built.strip()})"
        if new_version:
            return "Mise à jour installée", f"MediaTracker {new_version} est en service{extra}."
        return "Mise à jour installée", f"Une nouvelle version de MediaTracker est en service{extra}."
    if event_type == "system.error":
        ref = payload.get("correlation_id")
        route = payload.get("route") or "route inconnue"
        method = payload.get("method") or "?"
        kind = payload.get("exception_type") or "Exception"
        detail_msg = payload.get("exception_message")
        suffix = f" (réf. {ref})" if ref else ""
        if isinstance(detail_msg, str) and detail_msg.strip():
            return (
                "Erreur serveur",
                f"{method} {route} — {kind} : {detail_msg.strip()}{suffix}.",
            )
        return (
            "Erreur serveur",
            f"{method} {route} — {kind}{suffix}.",
        )
    if event_type == "jellyfin_history.import_failed":
        kind = payload.get("exception_type") or "Exception"
        detail_msg = str(payload.get("message") or "Import historique Jellyfin en échec.")
        return "Import historique Jellyfin", f"{kind} : {detail_msg}"
    if event_type == "catalog.failed":
        detail = str(payload.get("message") or "Rafraîchissement du catalogue impossible.")
        return "Catalogue en échec", detail
    if event_type.startswith("hive.transfer."):
        detail = str(
            payload.get("message") or payload.get("reason") or "Événement transfert Hive (historique)."
        )
        return "Transfert Hive", detail
    if event_type == "media.unfollowed":
        automatic = payload.get("automatic") is True
        reason = str(payload.get("reason") or "")
        if automatic or reason == "complete":
            detail = payload.get("message")
            if isinstance(detail, str) and detail.strip():
                return "Fin du suivi (automatique)", detail.strip()
            via = str(payload.get("via") or "")
            if via == "watched":
                return (
                    "Fin du suivi (automatique)",
                    f"{show} : série terminée, tous les épisodes diffusés ont été vus.",
                )
            if via == "downloaded":
                return (
                    "Fin du suivi (automatique)",
                    f"{show} : série terminée, tous les épisodes diffusés sont téléchargés.",
                )
            return "Fin du suivi (automatique)", f"{show} : série terminée et complète."
        if reason == "user":
            return "Fin du suivi", f"{show} n'est plus suivi."
        detail = payload.get("message")
        if isinstance(detail, str) and detail.strip():
            return "Fin du suivi", detail.strip()
        return "Fin du suivi", f"{show} n'est plus suivi."
    if event_type == "notify.test":
        return "Test MediaTracker", "Notification de test. Aucun fichier n'a été déposé."
    if event_type == "jellyfin.webhook_token_revealed":
        return "Jeton webhook Jellyfin", "Affichage du jeton webhook Jellyfin (valeur non journalisée)."
    if event_type == "jellyfin.webhook_token_regenerated":
        return "Jeton webhook Jellyfin", "Régénération du jeton webhook Jellyfin (nouvelle valeur non journalisée)."
    if event_type == "jellyfin_history.imported":
        marked = payload.get("marked") if isinstance(payload.get("marked"), int) else payload.get("lines")
        created = payload.get("created") if isinstance(payload.get("created"), int) else 0
        unmatched = payload.get("unmatched") if isinstance(payload.get("unmatched"), int) else 0
        return (
            "Import historique Jellyfin",
            f"{marked or 0} marquage(s), {created} fiche(s) créée(s), {unmatched} ligne(s) non identifiée(s).",
        )
    subject = f"{show} — {code}" if code else show
    return "MediaTracker", f"{subject} — {event_type}"


def _nouveautes(payload: dict[str, Any]) -> tuple[str, str]:
    count = payload.get("count") if isinstance(payload.get("count"), int) else 0
    names: list[str] = []
    for item in payload.get("items") or []:
        if isinstance(item, dict) and item.get("title"):
            names.append(str(item["title"]))
    if not count:
        count = len(names)
    if names:
        shown = ", ".join(names[:8])
        extra = f" (+{count - len(names[:8])})" if count > 8 else ""
        return "Nouveautés", f"{count} nouveautés : {shown}{extra}."
    return "Nouveautés", f"{count} nouveautés."


def _grouped(event_type: str, payload: dict[str, Any]) -> tuple[str, str]:
    count = payload.get("count") if isinstance(payload.get("count"), int) else 0
    if event_type == "library.imported":
        return "Fichiers rangés", f"{count} fichiers sont disponibles sur le NAS."
    if event_type == "item.available":
        return "Nouveaux épisodes", f"{count} épisodes sont disponibles et n'ont pas été téléchargés."
    if event_type == "cleanup.upcoming":
        return "Nettoyage à venir", f"{count} éléments seront mis à la corbeille."
    if event_type == "cleanup.done":
        reason = str(payload.get("reason") or "auto")
        noun = "fichier" if count == 1 else "fichiers"
        if reason == "manual":
            return "Nettoyage manuel", f"{count} {noun} mis à la corbeille."
        if reason == "duplicate":
            return "Doublon retiré", f"{count} {noun} doublon(s) retiré(s) de la bibliothèque."
        return "Nettoyage automatique", f"{count} {noun} mis à la corbeille (nettoyage automatique)."
    return "MediaTracker", f"{count} notifications ({event_type})."


def _cleanup_done(payload: dict[str, Any], show: str, code: str) -> tuple[str, str]:
    reason = str(payload.get("reason") or "auto")
    count = payload.get("count") if isinstance(payload.get("count"), int) else 1
    noun = "fichier" if count == 1 else "fichiers"
    amount = str(payload.get("size_label") or "").strip()
    extra = f" ({amount})" if amount else ""
    target = f"{show} — {code}" if code else show
    if isinstance(payload.get("season"), int) and not code:
        target = f"{show} — saison {payload['season']}"
    if reason == "manual":
        return "Nettoyage manuel", f"{target} : {count} {noun} mis à la corbeille manuellement{extra}."
    if reason == "duplicate":
        return "Doublon retiré", f"{target} : {count} {noun} doublon(s) retiré(s){extra}."
    return "Nettoyage automatique", f"{target} : {count} {noun} mis à la corbeille (automatique){extra}."


def _code(payload: dict[str, Any]) -> str:
    season = payload.get("season")
    episode = payload.get("episode")
    if isinstance(season, int) and isinstance(episode, int):
        return f"S{season:02d}E{episode:02d}"
    return ""
