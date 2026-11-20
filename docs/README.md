# Documentation utilisateur MediaTracker

MediaTracker est une application **mono-utilisateur** pour NAS : suivre films et séries (TMDB), chercher des liens, télécharger via AllDebrid et aria2, ranger les fichiers et les exposer à Jellyfin. Interface web en français.

Image Docker publique : `ghcr.io/nico2511/mediatracker-hive:latest`.

## Sommaire

### Démarrage

| Document | Contenu |
| --- | --- |
| [Installation](installation.md) | Docker Compose, volumes, variables d’environnement essentielles |
| [Premier démarrage](premier-demarrage.md) | Assistant `/configuration`, compte admin, `SETUP_TOKEN` |
| [Exemples de configuration](exemples/README.md) | Compose commentés (NAS simple, Jellyfin, Watchtower séparé, deux instances Hive) |

### Utilisation

| Document | Contenu |
| --- | --- |
| [Chaîne de téléchargement](telechargements.md) | Sources, ordre, AllDebrid, aria2, rangement, corbeille |
| [Médias](medias.md) | Liste des fiches, filtres, fiche film/série, suppression en masse |
| [TMDB](tmdb.md) | Jeton, catalogue, suivi, correction d’identifiant |
| [Jellyfin](jellyfin.md) | Synchro, webhook, chemins, import historique |
| [Hive](hive.md) | Ruches, membres, partage, débits, téléchargements entre instances |
| [Home Assistant](home-assistant.md) | Package, jetons, tableaux de bord |

### Réglages (une section = une page de l’app)

Index : [Réglages](reglages/README.md) — URLs du type `http://<hôte>:8080/reglages/<section>`.

| Section | Fichier |
| --- | --- |
| Compte | [reglages/compte.md](reglages/compte.md) |
| Profils de qualité | [reglages/qualite.md](reglages/qualite.md) |
| Sources | [reglages/sources.md](reglages/sources.md) |
| Réglages de scan | [reglages/scan.md](reglages/scan.md) |
| Jellyfin | [reglages/jellyfin.md](reglages/jellyfin.md) |
| Notifications | [reglages/notifications.md](reglages/notifications.md) |
| Corbeille | [reglages/corbeille.md](reglages/corbeille.md) |
| AllDebrid | [reglages/alldebrid.md](reglages/alldebrid.md) |
| Disque et sauvegardes | [reglages/disque.md](reglages/disque.md) |
| Hive | [reglages/hive.md](reglages/hive.md) |
| Application | [reglages/application.md](reglages/application.md) |
| Mise à jour et redémarrage | [reglages/maj.md](reglages/maj.md) |
| API et jetons | [reglages/jetons.md](reglages/jetons.md) |
| Réinitialiser la base | [reglages/reset.md](reglages/reset.md) |

### Maintenance

| Document | Contenu |
| --- | --- |
| [Mises à jour](mises-a-jour.md) | Watchtower, bouton « Mettre à jour maintenant » |
| [Sauvegarde et restauration](sauvegarde.md) | `/config/backups`, téléchargement, import et restauration depuis l’UI |
| [Dépannage et FAQ](depannage-faq.md) | Santé, disques, erreurs courantes |

### Autre

- [README racine](../README.md) : vue d’ensemble courte et commandes développeur.

## Règle de montage (indispensable)

Montez **un seul volume données** pour bibliothèque et téléchargements (ex. `/chemin/data:/data`). Les déplacements atomiques après téléchargement exigent le **même système de fichiers**. Le volume **`/config`** reste séparé (base SQLite, journaux, sauvegardes, secret aria2).

Sans variables `MOVIES_DIR` / `SERIES_DIR` / `DOWNLOADS_DIR` / `TRASH_DIR`, les défauts sous `DATA_DIR=/data` sont :

- Films : `/data/media/Films`
- Séries : `/data/media/Series`
- Téléchargements : `/data/downloads`
- Corbeille : `/data/.trash`
