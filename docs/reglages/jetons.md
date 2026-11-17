# Services et jetons

URL : `/reglages/jetons`

Un **service par carte**. Les secrets sortants (TMDB, AllDebrid, Jellyfin, GitHub, Watchtower) ne sont **jamais réaffichés** après enregistrement (chiffrement en base). Pour Watchtower, utilisez le jeton du conteneur (`WATCHTOWER_HTTP_API_TOKEN`), pas un jeton **Accès API** MediaTracker.

## TMDB

- **Jeton de lecture** (`tmdb_read_token`).
- **Déverrouille** : écrans Découvrir et Nouveautés, identification TMDB des médias et de la bibliothèque disque.
- Sans jeton : pas de recherche catalogue ni d’identification distante TMDB.

## AllDebrid

- Connexion via **clé API** ou flux **PIN** (bloc compte sur la carte).
- **Déverrouille** :
  - page [Sources](sources.md) : ordre des sources, cartes Wawacity / Free-Télécharger / Comet / Torrentio / addons Stremio, **rythme des requêtes** et **ordre des hébergeurs** ;
  - page [AllDebrid](alldebrid.md) : purge automatique après dépôt, purges manuelles ;
  - dans [Réglages de scan](scan.md) : options de téléchargement automatique des nouveaux suivis.
- **Masqué sans clé AllDebrid connectée** : les sources qui passent par AllDebrid n’apparaissent pas dans l’ordre ni dans les réglages détaillés ; un message oriente vers cette carte ou vers [Hive](hive.md). **Hive** reste visible si Hive est activé.
- Sans AllDebrid **ni** Hive utilisable, le téléchargement depuis les sources est bloqué (`download_ready` faux).

## Jellyfin

- **URL** et **clé API** (`jellyfin_url`, `jellyfin_api_key`).
- **Déverrouille** :
  - section [Jellyfin](jellyfin.md) : utilisateur, correspondance des chemins, webhook, synchro des vus, tests ;
  - bloc *Alignement Jellyfin* dans la **Bibliothèque** (onglet *À traiter*) ;
  - bouton **Importer l’historique** sur la carte (parcours détaillé dans [jellyfin.md](jellyfin.md) et [../jellyfin.md](../jellyfin.md)).
- Tant que l’URL ou la clé manquent, la section Jellyfin n’affiche que le renvoi vers cette carte.

## GitHub

- **Jeton GHCR (lecture)** (`ghcr_read_token`) : métadonnées de l’image pour comparer les versions.
- **Jeton GitHub (repli)** (`github_update_token`) : dernier commit sur la branche suivie si GHCR est indisponible.
- **Déverrouille** : comparaison de version et badge « nouvelle image » dans [Mise à jour](maj.md).

## Watchtower

- **URL** (`watchtower_url`) et **jeton** (`watchtower_token`), alignés sur l’API HTTP de Watchtower.
- **Déverrouille** : bouton **Mettre à jour maintenant** et interrupteur de notification après mise à jour automatique dans [Mise à jour](maj.md).
- Sans URL et jeton valides, la mise à jour immédiate reste indisponible (message dans l’interface).

## Accès API (entrant)

Jetons `mt_…` **créés dans MediaTracker** pour scripts et intégrations sur le LAN (Home Assistant, etc.).

- Portées : `read`, `write`, `admin`.
- Le secret n’est montré **qu’une fois** à la création.
- Ne sert **pas** pour Watchtower ni pour Jellyfin.