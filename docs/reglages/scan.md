# Réglages de scan

URL : `/reglages/scan`

## Scan des sources (séries / films suivis)

| Clé | Défaut | Plage | Rôle |
| --- | --- | --- | --- |
| `scan_interval_min` | 720 | 60–10080 | Intervalle entre scans (minutes) |
| `scan_ongoing_only` | true | bool | Ne repoller que les séries encore en cours |
| `scan_air_window_days` | 3 | 0–30 | Fenêtre autour de la date de diffusion (séries) |
| `film_theatrical_fallback_months` | 4 | 0–24 | Mois après sortie salle si TMDB sans date numérique |

L’ordre des sources se règle dans [Sources](sources.md).

Sans téléchargement disponible (AllDebrid absent et Hive inutilisable), l’option **Séries en cours seulement** et le bloc **Téléchargement automatique pour les nouveaux suivis** sont masqués avec un renvoi vers [Services et jetons](jetons.md).

## Chemins (absolus dans le conteneur)

| Clé | Défaut si vide (`DATA_DIR=/data`) |
| --- | --- |
| `media_dir` | `/data/media` |
| `library_movies_path` | `/data/media/Films` (surcharge env `MOVIES_DIR`) |
| `library_series_path` | `/data/media/Series` (surcharge env `SERIES_DIR`) |
| `downloads_dir` | `/data/downloads` |

Le dossier corbeille (`trash_dir`) se règle dans [Corbeille](corbeille.md).

L’UI affiche les chemins **en vigueur** et un **warning** si les volumes ne permettent pas les renommages atomiques.

## Bibliothèque disque

| Clé | Défaut | Rôle |
| --- | --- | --- |
| `library_scan_interval_hours` | 24 | Repasse planifiée (1–168 h) |
| `library_scan_enabled` | true | Active la repasse automatique |
| `library_auto_follow` | false | Suivre automatiquement après identification TMDB |
| `auto_download_default` | true | Téléchargement auto pour les nouveaux suivis (masqué sans source de téléchargement) |

Une repasse a aussi lieu après chaque fichier déposé. L’action après téléchargement terminé (`downloads_completed_action`) se règle dans [Corbeille](corbeille.md).

## Bibliothèque (onglets)

Écran **Bibliothèque** (`/bibliotheque`) : trois onglets (compteurs dans l’interface).

| Onglet | Rôle |
| --- | --- |
| **À traiter** | Dossiers à ranger (films / séries), fichiers suspects, regroupements, alignement Jellyfin si connecté, renommages en attente |
| **Nettoyage** | Dossiers vides, téléchargements incomplets (aperçu avant envoi à la corbeille si l’option est activée dans [Corbeille](corbeille.md)) |
| **Contenu** | Films et séries déjà conformes, avec filtres et tri |

L’URL peut mémoriser l’onglet (`?onglet=nettoyage`, etc.). Le paramètre `incomplets=corbeille` ouvre directement *Nettoyage*.