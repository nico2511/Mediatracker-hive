# Jellyfin

**Jellyfin** est un serveur multimédia qui lit les fichiers vidéo de votre bibliothèque. MediaTracker peut y **déposer** les téléchargements terminés, **rafraîchir** les bibliothèques et synchroniser l’état **vu / non vu** dans les deux sens.

## Configuration

**Réglages → Jellyfin** (`/reglages/jellyfin`) :

- **URL** : adresse du serveur Jellyfin. Sur le même fichier Docker Compose, souvent `http://jellyfin:8096`.
- **Clé API** : tableau de bord Jellyfin → engrenage → **Clés API**.
- **Utilisateur** : compte Jellyfin dont l’état « vu » est synchronisé avec MediaTracker.
- **Correspondance des chemins** : paires `préfixe_jellyfin=chemin_dans_mediatracker`, séparées par `;`.  
  Exemple avec l’image linuxserver : `/data/movies=/data/Films;/data/tvshows=/data/Séries`.
- **Ancien préfixe** : valeur de secours si une correspondance ne couvre pas un chemin (`jellyfin_path_prefix`).
- **Renommage automatique** : désactivé par défaut ; renomme les dossiers déjà identifiés sans validation manuelle.
- **Pousser un vu manuel** : optionnel, désactivé par défaut.
- **Synchro « vu » (minutes)** : défaut 30 ; le **webhook** Jellyfin accélère la mise à jour si vous l’installez.

Compose d’exemple avec Jellyfin : [`docker-compose.jellyfin.yml`](../docker-compose.jellyfin.yml) et [exemples](exemples/README.md).

## Webhook Jellyfin → MediaTracker

L’interface affiche l’URL du webhook et un jeton (en-tête `X-MediaTracker-Token`, prioritaire sur `?token=`). Événements pris en charge : PlaybackStop, UserDataSaved, ItemAdded, ItemUpdated.

Après chaque fichier déposé, MediaTracker demande aussi un **rafraîchissement** de la bibliothèque Jellyfin (délai d’environ une minute).

## Bibliothèque locale

- **Bibliothèque** : scan des dossiers films / séries, identification TMDB.
- **Suivre automatiquement après identification** : réglage dans **Scan** (`library_auto_follow`, désactivé par défaut).
- **Repasse** : intervalle en heures, plus une repasse après chaque dépôt.

## Importer l’historique « vu » depuis Jellyfin

MediaTracker peut reprendre ce que vous aviez déjà marqué comme **vu** dans Jellyfin, sans rester connecté au serveur Jellyfin pendant l’import.

**Entrée dans l’application** : **Réglages → API et jetons**, carte Jellyfin — bouton **Importer l’historique** (après URL et clé API). Voir aussi [Réglages — Jellyfin](reglages/jellyfin.md).

Deux modes sur l’écran d’import :

- **Bases Jellyfin** (recommandé) ;
- **Fichier CSV** (export préalable ou archivage).

### Avant de copier des fichiers Jellyfin

Sur le serveur Jellyfin (ou une sauvegarde arrêtée), les données utiles se trouvent en général dans le répertoire de données Jellyfin, sous-dossier `data/` :

| Fichier | Rôle |
| --- | --- |
| `jellyfin.db` | Base principale (comptes, bibliothèque, marquages vus, journal d’activité) |
| Archive `.zip` (tableau de bord → Sauvegardes) | Export Jellyfin 10.11 avec fichiers JSON (`Users`, `UserData`, `BaseItems`, `ActivityLogs`, …) |

**Conseil :** arrêtez le conteneur Jellyfin ou travaillez sur une **copie** des fichiers. Les outils MediaTracker ouvrent les bases en **lecture seule**.

### Parcours guidé (onglet Bases Jellyfin)

1. **Importer l’historique** (depuis la carte Jellyfin des réglages) → mode *Bases Jellyfin*.
2. Envoyez une copie de **jellyfin.db** **ou** une archive **.zip** de sauvegarde Jellyfin 10.11.
3. Choisissez le **compte Jellyfin** à importer.
4. **Prévisualisation** : compteurs par source (bibliothèque, journal d’activité, éléments sans titre) et compteurs d’identification dans MediaTracker. Vous pouvez **télécharger le CSV** généré pour vos archives.
5. **Confirmer** : marquage « vu » avec la date d’origine ; création éventuelle de fiches **sans suivi automatique**.
6. **Réactiver le suivi** : case à cocher par titre du dernier import, puis **Appliquer le suivi**.

Les fichiers envoyés sont stockés temporairement dans le dossier de configuration MediaTracker, vérifiés (SQLite ou ZIP), puis **supprimés** après confirmation, abandon ou erreur. Les restes de plus de 24 h sont nettoyés au démarrage. Taille maximale par fichier : **1 Go** par défaut (réglage `JELLYFIN_HISTORY_UPLOAD_MAX_BYTES`).

Derrière un **proxy inverse**, augmentez la limite de taille des requêtes si besoin (par exemple `client_max_body_size` côté nginx).

### Export CSV en ligne de commande (optionnel)

Depuis une machine avec Python 3, à la racine du dépôt :

```bash
python3 tools/jellyfin_history_export.py \
  --jellyfin-db /chemin/vers/copie/jellyfin.db \
  --user NomDuCompte \
  --out historique-jellyfin.csv
```

`--user` est facultatif s’il n’y a qu’un seul compte. Le parcours guidé dans l’interface s’appuie surtout sur le **journal d’activité** Jellyfin ; l’export CLI lit directement `jellyfin.db`.

Colonnes du CSV (UTF-8) : `type`, `title`, `year`, `series_title`, `season`, `episode`, identifiants externes (`tmdb_id`, etc.), `jellyfin_item_id`, `watched_at`, `play_count`, `source`.

### Import CSV (onglet secondaire)

1. **Importer l’historique** → mode *Fichier CSV*.
2. **Prévisualisation** puis **Confirmer** : même logique idempotente (réimporter le même fichier ne crée pas de doublons).
3. **Réactiver le suivi** : liste à cocher du **dernier** import uniquement.

Chaque import confirmé ajoute **une** entrée dans le journal applicatif, pas une par ligne.

## Dépannage

- L’écran **Santé** signale les dossiers sur des volumes différents ou des chemins incohérents.
- Boutons **Tester la connexion** et **Synchroniser les vus** dans **Réglages → Jellyfin**.
