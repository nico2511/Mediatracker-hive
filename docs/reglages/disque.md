# Disque et sauvegardes

URL : `/reglages/disque`

| Clé | Défaut | Plage | Rôle |
| --- | --- | --- | --- |
| `disk_min_free_gb` | 5 | 0–1024 | Pause téléchargements si espace libre insuffisant (`0` = off) |
| `backup_keep` | 7 | 1–90 | Sauvegardes SQLite quotidiennes dans `/config/backups` |

Carte espace disque (données et config). Bloc **Sauvegarde / restauration** de la base.

Voir [Sauvegarde](../sauvegarde.md).
