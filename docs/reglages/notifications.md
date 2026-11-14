# Notifications

URL : `/reglages/notifications`

| Clé | Rôle |
| --- | --- |
| `public_base_url` | Lien vers la fiche dans les messages (souvent URL LAN) |
| `notify_group_seconds` | Regroupe les fin de téléchargement (0 = désactivé) |
| `notify_quiet_start` / `notify_quiet_end` | Heures calmes `HH:MM`, fuseau `notify_timezone` (défaut Europe/Paris) |

Cibles : **Home Assistant** (webhook sans HMAC), **ntfy**, **webhook signé** HMAC-SHA256. Événements sélectionnables par cible (nouveautés décoché par défaut à la création).
