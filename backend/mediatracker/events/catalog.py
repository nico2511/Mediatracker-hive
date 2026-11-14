"""Outbox event names. Webhook subscriptions must use this set."""

# library.imported fires when a file has landed on the NAS (ntfy and Home Assistant).
LIBRARY_IMPORTED = "library.imported"

# Toggleable notifications. Other event types stay in the log.
NOTIFY_EVENTS: tuple[tuple[str, str], ...] = (
    ("item.available", "Nouveau média disponible"),
    (LIBRARY_IMPORTED, "Téléchargement terminé et rangé"),
    ("download.failed", "Échec définitif de téléchargement"),
    ("debrid.auth_blocked", "AllDebrid : confirmation e-mail requise"),
    ("source.error", "Source en erreur prolongée"),
    ("source.outage", "Source ou hébergeur en panne"),
    ("source.recovered", "Source ou hébergeur rétabli"),
    ("season.announced", "Nouvelle saison annoncée"),
    ("cleanup.upcoming", "Nettoyage à venir"),
    ("cleanup.done", "Nettoyage effectué"),
    ("cleanup.pass", "Passage du nettoyage"),
    ("cleanup.failed", "Nettoyage impossible"),
    ("disk.low", "Espace disque insuffisant"),
    ("library.scan_suspended", "Scan bibliothèque suspendu"),
    ("update.available", "Mise à jour de l'image"),
    ("nouveautes", "Nouveautés"),
)

# Left unchecked when a notification target is created. The user opts in.
NOTIFY_DEFAULT_OFF = frozenset({"nouveautes", "cleanup.pass"})

# Several of these inside the grouping window become one notification.
GROUPABLE_EVENTS = frozenset(
    {
        "item.available",
        LIBRARY_IMPORTED,
        "cleanup.upcoming",
        "cleanup.done",
    }
)

PROLONGED_SOURCE_STREAK = 3

EVENT_TYPES = frozenset(
    {
        "media.followed",
        "media.unfollowed",
        "media.series_status_changed",
        "item.available",
        "download.queued",
        "download.forced",
        "download.redownload",
        "download.started",
        "download.completed",
        "download.failed",
        "download.hoster_switched",
        "download.source_switched",
        "download.source_attempt",
        "download.source_error",
        LIBRARY_IMPORTED,
        "library.scanned",
        "library.scan_suspended",
        "library.scan_resumed",
        "library.verify_all.progress",
        "library.verify_all.completed",
        "library.deleted",
        "library.file_suspect",
        "media.repaired",
        "jellyfin.identity_pushed",
        "jellyfin.webhook_token_revealed",
        "jellyfin.webhook_token_regenerated",
        "jellyfin_history.imported",
        "jellyfin_history.import_failed",
        "item.watched",
        "aria2.unreachable",
        "aria2.reachable",
        "provider.health_changed",
        "source.checked",
        "source.domain_proposed",
        "source.domain_applied",
        "source.domain_auto_migrated",
        "source.domain_reverted",
        "source.redirect_blocked",
        "debrid.auth_blocked",
        "debrid.hoster_skipped",
        "source.error",
        "source.outage",
        "source.recovered",
        "season.announced",
        "cleanup.upcoming",
        "cleanup.done",
        "cleanup.pass",
        "cleanup.failed",
        "cleanup.missing",
        "cleanup.incomplete_orphans",
        "trash.purged",
        "trash.emptied",
        "incomplete.cleaned",
        "disk.low",
        "update.available",
        "system.updated",
        "system.error",
        "catalog.failed",
        "nouveautes",
    }
)
