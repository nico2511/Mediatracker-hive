# Nettoyage

URL : `/reglages/nettoyage`

| Clé | Défaut | Plage | Rôle |
| --- | --- | --- | --- |
| `incomplete_cleanup_enabled` | false | bool | Corbeille pour incomplets / orphelins (après aperçu Bibliothèque) |
| `auto_delete_enabled` | true | bool | Suppression auto du contenu vu |
| `auto_delete_days` | 14 | 1–365 | Délai après visionnage |
| `cleanup_notice_hours` | 24 | 1–168 | Préavis notification |
| `trash_retention_days` | 7 | 1–90 | Rétention en corbeille avant purge |

UI : aperçu prochain passage, liste corbeille (restaurer / supprimer définitif).
