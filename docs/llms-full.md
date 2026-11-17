# MediaTracker — documentation pour assistants IA

MediaTracker est une application **mono-utilisateur** pour NAS (souvent OpenMediaVault) : suivre des films et
séries via TMDB, chercher des liens sur des sources configurées, télécharger via **AllDebrid** et **aria2** (RPC
interne au conteneur), ranger les fichiers sur le disque et les exposer à **Jellyfin** (optionnel). Notifications
via webhook (ex. Home Assistant). Interface web en français, thème sombre ; déploiement typique en **une image
Docker** (`ghcr.io/nico2511/mediatracker-hive`).

**Pile technique :** API **FastAPI** (Python 3.12+), base **SQLite**, workers et intégrations dans `backend/` ;
interface **Vue** dans `frontend/` ; conteneur unique avec aria2 non exposé sur le réseau.

**Parcours d’un téléchargement (résumé) :** un scan ou une action manuelle interroge les **sources** dans l’ordre
configuré ; le lien retenu respecte le **profil de qualité** et les préférences d’hébergeurs ; **AllDebrid** débloque
le lien ; **aria2** télécharge en local ; à la fin, le fichier est **déplacé** vers Films ou Séries (même système de
fichiers que les téléchargements), la bibliothèque est rescannée et Jellyfin peut être notifié.

Ce fichier est généré automatiquement à partir de `docs/` (hors interne, archives et exemples). Régénération :
`python scripts/build_ai_doc.py`.

## Sommaire

- [Installation](#docs-installation) — `docs/installation.md`
- [Premier démarrage](#docs-premier-demarrage) — `docs/premier-demarrage.md`
- [Chaîne de téléchargement](#docs-telechargements) — `docs/telechargements.md`
- [Réglages](#docs-reglages-README) — `docs/reglages/README.md`
- [Compte](#docs-reglages-compte) — `docs/reglages/compte.md`
- [Profils de qualité](#docs-reglages-qualite) — `docs/reglages/qualite.md`
- [Sources](#docs-reglages-sources) — `docs/reglages/sources.md`
- [Réglages de scan](#docs-reglages-scan) — `docs/reglages/scan.md`
- [Jellyfin](#docs-reglages-jellyfin) — `docs/reglages/jellyfin.md`
- [Notifications](#docs-reglages-notifications) — `docs/reglages/notifications.md`
- [Corbeille](#docs-reglages-corbeille) — `docs/reglages/corbeille.md`
- [AllDebrid](#docs-reglages-alldebrid) — `docs/reglages/alldebrid.md`
- [Disque et sauvegardes](#docs-reglages-disque) — `docs/reglages/disque.md`
- [Hive](#docs-reglages-hive) — `docs/reglages/hive.md`
- [Application](#docs-reglages-application) — `docs/reglages/application.md`
- [Mise à jour et redémarrage](#docs-reglages-maj) — `docs/reglages/maj.md`
- [Services et jetons](#docs-reglages-jetons) — `docs/reglages/jetons.md`
- [Réinitialiser la base](#docs-reglages-reset) — `docs/reglages/reset.md`
- [Médias](#docs-medias) — `docs/medias.md`
- [TMDB](#docs-tmdb) — `docs/tmdb.md`
- [Jellyfin](#docs-jellyfin) — `docs/jellyfin.md`
- [Hive](#docs-hive) — `docs/hive.md`
- [Home Assistant](#docs-home-assistant) — `docs/home-assistant.md`
- [Mises à jour](#docs-mises-a-jour) — `docs/mises-a-jour.md`
- [Sauvegarde et restauration](#docs-sauvegarde) — `docs/sauvegarde.md`
- [Dépannage et FAQ](#docs-depannage-faq) — `docs/depannage-faq.md`

## Installation

Chemin source : `docs/installation.md`

<a id="docs-installation"></a>

Ce guide décrit l’installation de MediaTracker avec **Docker** et **Docker Compose** sur un NAS ou un serveur Linux.

### Prérequis

- **Docker** et **Docker Compose** installés sur la machine hôte.
- Une image récupérable depuis le registre : `ghcr.io/nico2511/mediatracker-hive:latest`.
- Un disque (ou partage) pour le volume de **configuration** et un montage **unique** pour les **données** média (voir ci-dessous).

### Fichiers Compose fournis

À la racine du dépôt :

| Fichier | Usage |
| --- | --- |
| [`docker-compose.yml`](../docker-compose.yml) | MediaTracker seul |
| [`docker-compose.jellyfin.yml`](../docker-compose.jellyfin.yml) | MediaTracker + Jellyfin (linuxserver) sur le même réseau Compose |
| [Exemples commentés](exemples/README.md) | Variantes NAS, réseau externe, Watchtower, Hive multi-instances |

Exemples prêts à copier : dossier [`docs/exemples/`](exemples/).

### Volumes

```yaml
volumes:
  - /chemin/vers/mtr/config:/config
  - /chemin/vers/medias:/data
```

- **`/config`** : base SQLite (`app.db`), journaux rotatifs, sauvegardes quotidiennes, fichier `aria2.secret`, cache d’affiches TMDB.
- **`/data`** : bibliothèque, dossier de téléchargements et corbeille — **tout sur le même montage**.

`PUID` / `PGID` : identifiants utilisateur et groupe pour les fichiers créés dans le conteneur (`id -u` / `id -g` sur l’hôte).

### Variables d’environnement obligatoires et utiles

Copiez [`.env.example`](../.env.example) vers un fichier non versionné (ex. `mediatracker.env`) ou définissez les variables dans Compose.

| Variable | Rôle |
| --- | --- |
| `SECRET_KEY` | Obligatoire (≥ 16 caractères). Chiffre les secrets en base. Ne pas changer sans ressaisir les clés. Génération : `python -c "import secrets; print(secrets.token_urlsafe(32))"` |
| `ADMIN_USERNAME` / `ADMIN_PASSWORD` | Optionnels : si vide, l’assistant crée le compte. |
| `SETUP_TOKEN` | Optionnel : jeton exigé pour le premier compte ; sinon généré au démarrage (voir journaux). |
| `MOVIES_DIR`, `SERIES_DIR`, `DOWNLOADS_DIR`, `TRASH_DIR` | Chemins **dans le conteneur** ; surcharge des défauts sous `/data`. |
| `JELLYFIN_URL`, `JELLYFIN_PATH_MAP` | Pré-remplissage Jellyfin (sinon saisi dans l’assistant ou Réglages). |
| `PUBLIC_URL` | URL publique sans chemin (`https://mediatracker.example.com`). Alias : `PUBLIC_BASE_URL`. |
| `FORWARDED_ALLOW_IPS` | IP du reverse proxy pour `X-Forwarded-*` (défaut `127.0.0.1`). |
| `COOKIE_SECURE` | `auto`, `true` ou `false` pour le cookie de session. |
| `TZ`, `PUID`, `PGID`, `UMASK` | Fuseau et droits fichiers. |

Les clés **TMDB**, **AllDebrid**, **Jellyfin** et les jetons Watchtower ne doivent **pas** figer dans un compose versionné : saisissez-les dans l’assistant ou **Réglages** (chiffrés ensuite).

Au **premier démarrage**, les réglages listés dans `settings_store.SETTING_SPECS` sont copiés depuis l’environnement vers la base ; les modifications ultérieures se font dans l’UI (sauf secrets uniquement en env si non encore créés en base).

Liste commentée complète : [`.env.example`](../.env.example) et [exemples `.env`](exemples/env.example).

### Démarrage

```bash
docker compose up -d
## ou
docker compose -f docker-compose.jellyfin.yml up -d
```

Ouvrez `http://<hôte>:8080`. Si aucun compte n’existe, l’assistant redirige vers `/configuration`.

### Points de contrôle

- Santé publique du conteneur : `GET /healthz`
- Santé applicative (session requise) : page **Santé** ou `GET /api/v1/health`
- Documentation OpenAPI : `/api/docs` (session requise)

Suite : [Premier démarrage](#docs-premier-demarrage).

## Premier démarrage

Chemin source : `docs/premier-demarrage.md`

<a id="docs-premier-demarrage"></a>

### Assistant de configuration

Route : `/configuration` (accessible sans session tant qu’aucun compte n’existe, ou pour terminer l’assistant).

Étapes affichées dans l’interface :

1. **Mot de passe** — création du compte `admin` (ou `ADMIN_USERNAME`) ; `setup_token` si le serveur l’exige.
2. **Dossiers** — `media_dir`, films, séries, téléchargements, corbeille ; avertissement si les chemins ne sont pas sur le même volume.
3. **AllDebrid** — clé API (optionnelle à ce stade, stockée chiffrée).
4. **TMDB** — jeton de lecture API v4.
5. **Jellyfin** — URL, clé API, utilisateur, correspondance de chemins.
6. **Sources** — URLs optionnelles des sources de liens (voir ci-dessous). Bouton **Passer** : rien n’est modifié.
7. **Home Assistant** — webhook optionnel (URL + identifiant de webhook).
8. **Terminer** — récapitulatif du scan initial.

#### Étape Sources

Champs **tous optionnels** ; une valeur vide laisse le réglage actuel ou le défaut du conteneur :

| Champ assistant | Stockage (même clé que Réglages → Sources) |
| --- | --- |
| Wawacity | `wawacity_base_url` |
| Free-Télécharger | URL dans la fiche fournisseur `free_telecharger` (pas de clé SQLite dédiée) |
| Comet (une ligne par instance) | `comet_base_urls` |
| Torrentio | `torrentio_base_url` |
| Addons Stremio (optionnel) | `stremio_addons` |

Validation : chaque adresse renseignée doit commencer par `http://` ou `https://`. **Tester Wawacity** appelle le même test que dans l’assistant historique (`POST /api/v1/setup/probe`). Enregistrement groupé : `POST /api/v1/setup/sources`.

Hive n’a pas d’URL de base (activation et adresse publique dans **Réglages → Hive**).

Si un mot de passe administrateur existe déjà (variable `ADMIN_PASSWORD` au démarrage), l’assistant commence à l’étape **Dossiers** après connexion.

#### Jeton d’installation (`SETUP_TOKEN`)

Sans compte et sans `SETUP_TOKEN` en variable d’environnement, MediaTracker génère un jeton au démarrage et l’écrit dans les **journaux** (niveau WARNING), du type :

```text
Aucun compte administrateur : pour créer le premier compte via l'assistant,
saisissez le jeton d'installation suivant (ou définissez SETUP_TOKEN pour le fixer) : …
```

Définir `SETUP_TOKEN` dans l’environnement fixe ce jeton et évite de le chercher dans les logs.

### Première connexion

- URL : `http://<hôte>:8080/login`
- Après l’assistant : accueil, **Suivre** (TMDB), **Téléchargements**, **Bibliothèque**, **Réglages**.

### Réglages complémentaires

Tout ce qui n’est pas dans l’assistant se configure dans **Réglages** — voir [l’index des sections](#docs-reglages-README) :

- Ordre et activation des **sources** (`/reglages/sources`)
- **Profils de qualité** et repli global
- **Hive** (désactivé par défaut)
- **Watchtower** et jetons GHCR (`/reglages/jetons`, `/reglages/maj`)

### PWA

**Réglages → Application** propose d’installer MediaTracker comme application (navigateur compatible).

## Chaîne de téléchargement

Chemin source : `docs/telechargements.md`

<a id="docs-telechargements"></a>

### Vue d’ensemble

```mermaid
flowchart LR
  subgraph sources [Sources]
    W[Wawacity]
    F[Free-Télécharger]
    H[Hive]
    C[Comet]
    T[Torrentio]
    X[Addons Stremio]
  end
  subgraph mt [MediaTracker]
    S[Sélection lien / profil]
    A[AllDebrid unlock]
    R[aria2 RPC interne]
    P[Rangement bibliothèque]
  end
  sources --> S --> A --> R --> P
```

1. Un **scan** ou une action manuelle interroge les sources dans l’**ordre** défini (`download_source_order`).
2. Le lien retenu respecte le **profil de qualité** de la fiche, la **préférence d’hébergeurs** et les hébergeurs refusés par l’application (`BLOCKED_SOURCE_HOSTERS` — non configurables).
3. **AllDebrid** débloque le lien ; MediaTracker ne expose **pas** aria2 sur le réseau (RPC local + secret en `/config`).
4. À la fin, le fichier est **déplacé** vers Films / Séries (structure façon Jellyfin), la bibliothèque est rescannée, Jellyfin peut être notifié.

Page **Téléchargements** : file des jobs, progression aria2, actions d’annulation / relance selon l’état.

### Sources disponibles

Identifiants dans l’UI (**Réglages → Sources**) :

| Id | Rôle |
| --- | --- |
| `wawacity` | Scraping HTML ; URL de base requise |
| `free_telecharger` | Scraping ; URL via fiche fournisseur |
| `hive` | Pairs des ruches ; nécessite Hive activé et membre joignable |
| `comet` | Instance(s) Comet (liste d’URLs, défaut public possible) |
| `torrentio` | Torrentio / aimants |
| `stremio_addons` | Une ou plusieurs URLs d’addons Stremio (torrents et liens débridables selon la réponse de l’addon ; sans clé dans l’URL) |

Ordre par défaut (code) : `wawacity,free_telecharger,hive,comet,torrentio,stremio_addons`. Au moins une source doit rester activée.

Une ancienne entrée dédiée aux flux DDL UwU+ est **migrée automatiquement** vers la liste **Addons Stremio** (même URL, une ligne d’addon).

Réglages communs : délai entre requêtes (`source_request_delay_seconds`), cache (`source_cache_seconds`), backoff après erreur (`source_error_backoff_min`), délai entre recherches distantes par fiche (`remote_search_cooldown_hours`, voir [Sources](#docs-reglages-sources)), ordre des **hébergeurs** (`hoster_preference`).

#### Wawacity — films

Pour un **film**, la découverte passe d’abord par une recherche **sans filtre de liste** (`plain_search_url`) : titre français (avec `year=` quand l’année TMDB est connue, puis la même requête **sans** année), puis le titre original si besoin. Les **séries** conservent le modèle par listes `s=`.

Sur une fiche Wawacity ouverte, si l’en-tête ne correspond pas au profil de la fiche, le moteur peut ouvrir une **version sœur** proposée dans le bloc **« Autres qualités »** (qualité et langue lues sur le bouton, titre et année repris de la fiche courante).

### Journal des tentatives par source

Chaque passage sur une source pendant un job de téléchargement peut créer ou mettre à jour un événement **`download.source_attempt`** (un enregistrement par couple téléchargement × source). Le détail affiché reprend des libellés du type :

- `Wawacity : N lien(s) trouvé(s).`
- `Wawacity : aucun résultat.`
- `Wawacity : refusé (filtre profil).` (ou autre motif, ex. filtre de taille)
- `Comet : refusé (Aucun flux Comet ne correspond au profil « … ».)` lorsque les flux ne passent pas le profil
- `… : ignoré (…)` ou `… : erreur (…)`

Lorsque plusieurs sources ont été essayées dans le même passage, le message d’échec du job peut **concaténer** une ligne par source (séparateur ` · `). En l’absence de liens Wawacity en base, le worker peut **réveiller** un sondage source immédiat au lieu d’appliquer seul le long recul après erreur.

### Hive dans la file Download

Un téléchargement Hive passe par la **même file** qu’un lien AllDebrid : octroi pair (`fetch-grant`) et téléchargement HTTP via aria2 avec en-têtes dédiés — **pas** d’écran de transferts séparé. Lorsque plusieurs pairs proposent le même fichier, le choix tient compte de la **charge** signalée par les pairs et du débit mesuré ; en cas de pair occupé, repli vers un autre pair ou la source suivante. Détails : [Hive](#docs-hive).

### AllDebrid

- Clé API : assistant ou **Réglages → AllDebrid** (bloc compte + PIN éventuel).
- **Purger après dépôt** : retire le lien / l’aimant du compte une fois le fichier rangé (défaut activé).
- Téléchargements **manuels** : possible depuis une fiche si l’URL est autorisée (certains hébergeurs sont refusés côté serveur).

### aria2

- Processus dans le conteneur MediaTracker ; secret `aria2_rpc_secret` généré et écrit dans `/config/aria2.secret`.
- Alerte si aria2 reste injoignable pendant les téléchargements (`aria2_unreachable_alert_min`, défaut 15 minutes).

### Rangement « façon Jellyfin »

Après téléchargement réussi :

- Film : dossier sous le chemin **films** configuré.
- Série : structure saison / épisode sous le chemin **séries**.
- **Repasse bibliothèque** planifiée (`library_scan_interval_hours`) et après chaque dépôt.

**Réglages de scan** : chemins absolus, action après téléchargement (`archive` ou `remove` dans la liste UI uniquement — le fichier disque reste).

### Corbeille et suppression automatique

- Fichiers supprimés ou auto-nettoyés vont dans `trash_dir` (même volume que la bibliothèque).
- **Corbeille** : suppression auto après visionnage, préavis, rétention, conservation — voir [reglages/corbeille.md](#docs-reglages-corbeille).
- **Disque** : pause des téléchargements si espace libre sous le seuil (`disk_min_free_gb`, `0` désactive).

### Nouveautés catalogue

Worker séparé : films/séries « nouveautés » selon `catalog_interval_hours`, filtres qualité/langue, rétention `catalog_retention_days` (réglages en base, pas de page dédiée dans l’index Réglages — modifiable via API / env au premier boot).

Voir aussi : [Sources](#docs-reglages-sources), [Scan](#docs-reglages-scan), [AllDebrid](#docs-reglages-alldebrid).

## Réglages

Chemin source : `docs/reglages/README.md`

<a id="docs-reglages-README"></a>

Dans l’application : **Réglages** (`/reglages`), puis une section du menu. Chaque section a une URL directe du type `/reglages/<slug>`.

L’index est regroupé en cinq blocs (Général, Services et jetons, Téléchargement, Médiathèque, Stockage), comme dans l’interface.

Les clés listées dans les pages ci-dessous correspondent aux réglages persistés en base. Après le premier enregistrement dans l’interface, **les valeurs affichées en UI remplacent** les variables d’environnement de départ.

### Général

| Slug | Titre dans l’UI | Documentation |
| --- | --- | --- |
| `compte` | Compte | [compte.md](#docs-reglages-compte) |
| `application` | Application | [application.md](#docs-reglages-application) |
| `maj` | Mise à jour et redémarrage | [maj.md](#docs-reglages-maj) |

### Services et jetons

| Slug | Titre dans l’UI | Documentation |
| --- | --- | --- |
| `jetons` | Services et jetons | [jetons.md](#docs-reglages-jetons) |

### Téléchargement

| Slug | Titre dans l’UI | Documentation |
| --- | --- | --- |
| `sources` | Sources | [sources.md](#docs-reglages-sources) |
| `qualite` | Profils de qualité | [qualite.md](#docs-reglages-qualite) |
| `scan` | Réglages de scan | [scan.md](#docs-reglages-scan) |
| `alldebrid` | AllDebrid | [alldebrid.md](#docs-reglages-alldebrid) |
| `hive` | Hive | [hive.md](#docs-reglages-hive) |

Guide Hive (usage des ruches) : [../hive.md](#docs-hive).

### Médiathèque

| Slug | Titre dans l’UI | Documentation |
| --- | --- | --- |
| `jellyfin` | Jellyfin | [jellyfin.md](#docs-reglages-jellyfin) |
| `notifications` | Notifications | [notifications.md](#docs-reglages-notifications) |

Guide Jellyfin (webhook, import d’historique détaillé) : [../jellyfin.md](#docs-jellyfin).

### Stockage

| Slug | Titre dans l’UI | Documentation |
| --- | --- | --- |
| `disque` | Disque et sauvegardes | [disque.md](#docs-reglages-disque) |
| `corbeille` | Corbeille | [corbeille.md](#docs-reglages-corbeille) |
| `reset` | Réinitialiser la base | [reset.md](#docs-reglages-reset) |

### Hors menu Réglages (liés aux réglages)

- **Bibliothèque** (`/bibliotheque`) : trois onglets *À traiter*, *Nettoyage*, *Contenu* — voir [scan.md](#docs-reglages-scan).
- **Médias** (`/medias`) : liste des fiches, filtres et suppression en masse — voir [medias.md](#docs-medias).

L’URL `/reglages/nettoyage` redirige vers `/reglages/corbeille`.

## Compte

Chemin source : `docs/reglages/compte.md`

<a id="docs-reglages-compte"></a>

URL : `/reglages/compte`

- Changement de mot de passe (8 caractères minimum, mot de passe actuel requis).
- **Déconnecter toutes les autres sessions** : invalide les cookies sur les autres navigateurs ; la session courante reste active.

## Profils de qualité

Chemin source : `docs/reglages/qualite.md`

<a id="docs-reglages-qualite"></a>

URL : `/reglages/qualite`

### Repli global

- **`allow_fallback`** : valeur par défaut pour les nouvelles fiches (le repli par fiche peut différer).
- **`profile_fallback_order`** : ordre des profils de qualité essayés après l’échec du profil principal (liste d’ids TMDB internes).

### Profils

Chaque profil définit : nom, langues ordonnées, résolution min/max, taille min/max (Mo), mots exclus dans le nom de fichier. Un profil peut être **défaut**. Wawacity, Comet et Hive utilisent le même profil pour une fiche, y compris lors d’un contrôle source immédiat.

Langues acceptées : VFF, VFQ, VF, MULTI, VOSTFR, VO (TRUEFRENCH enregistré comme VFF).

### Profils intégrés

Au premier démarrage, la base reçoit une liste de profils intégrés. Les mises à jour ultérieures **ajoutent** seulement un profil intégré encore absent (sans réécrire un profil que vous avez déjà modifié).

| Nom | Rôle |
| --- | --- |
| **VF/Multi 1080p** | Profil **défaut** sur une **nouvelle** base : min et max à **1080p** strict (migration initiale et défaut code). |
| 4K Multi | 4K, langues MULTI/VFF/VF |
| 720p VF | HD (720p) |
| VOSTFR 1080p | VOSTFR, plafond 1080p |
| Meilleure dispo | Pas de borne de résolution |
| **HDLight VF/Multi** | Cible **HDLight1080** uniquement (HDLight 1080p), langues VFF/VFQ/VF/MULTI |

Dans le formulaire, les résolutions proposées incluent **HDLight** (720p léger historique), **HD** (720p), **HDLight1080**, **1080p** et **4K**.

### Échelle des résolutions

Le moteur classe les résolutions du plus bas au plus haut : SD, HDLight, HD, **HDLight1080**, 1080p, 4K. Une sortie HDLight 1080p est donc **au-dessus** du HD 720p, mais **en dessous** du 1080p « plein » pour le profil par défaut (qui exige 1080p et rejette HDLight1080).

## Sources

Chemin source : `docs/reglages/sources.md`

<a id="docs-reglages-sources"></a>

URL : `/reglages/sources`

Page dédiée (composant séparé de l’écran générique). Les mêmes URL peuvent être préremplies à l’étape **Sources** de l’assistant (`/configuration`) — voir [Premier démarrage](#docs-premier-demarrage).

Sans **AllDebrid connecté** ni source **Hive** utilisable, un bandeau bloque la page et renvoie vers [Services et jetons](#docs-reglages-jetons) ou [Hive](#docs-reglages-hive).

### Présentation

- Chaque source est une **carte** : interrupteur Activée / Désactivée, libellé lisible, courte description du rôle.
- Dans **Ordre des sources**, seules les sources **disponibles** (selon AllDebrid / Hive) se réordonnent ; les autres peuvent être masquées avec un message *Disponible après connexion d’AllDebrid*.
- Les URL longues (instances Comet, addons Stremio) sont tronquées avec **Voir** et **Copier**.

### Ordre et activation

- Clé **`download_source_order`** : liste ordonnée parmi `wawacity`, `free_telecharger`, `hive`, `comet`, `torrentio`, `stremio_addons`. Au moins une source doit rester activée dans l’ordre enregistré.
- État en ligne / panne : affiché sur chaque carte ; API `GET /api/v1/settings/sources/status`.

### Sans clé AllDebrid

- Sources **qui passent par AllDebrid** (Wawacity, Free-Télécharger, Comet, Torrentio, Stremio) : cartes et réglages détaillés masqués ou indisponibles ; elles ne sont pas proposées dans l’ordre affiché.
- **Hive** : reste configurable si Hive est activé dans [Hive](#docs-reglages-hive).
- Panneau **Rythme des requêtes** et **ordre des hébergeurs** : visible seulement si AllDebrid est connecté.

### Rythme et cache

| Clé | Défaut (env) | Plage | Rôle |
| --- | --- | --- | --- |
| `source_request_delay_seconds` | 5 | 2–60 | Pause entre requêtes HTTP vers les sources |
| `source_cache_seconds` | 900 | 0–3600 | Cache des résultats de recherche |
| `source_error_backoff_min` | 180 | 30–1440 | Repousse après erreur source |
| `remote_search_cooldown_hours` | 6 | 1–72 | Délai entre deux recherches distantes d’une même fiche |

`remote_search_cooldown_hours` s’applique **par fiche** : retéléchargement, repli de profil, relecture source, y compris actions manuelles (pas de dérogation si une recherche réseau est nécessaire).

### Hébergeurs (liens HTML)

- Clé **`hoster_preference`** : ordre des hébergeurs acceptés, liste réordonnable dans **Rythme des requêtes**.
- Noms proposés : liste statique de l’application **plus** hôtes publics AllDebrid (mis en cache côté serveur).
- **`uptobox`** : **hébergeur interdit** — absent des listes, refusé à l’enregistrement et ignoré à la sélection de liens, même s’il apparaît encore sur une page source.
- **`1fichier`** est placé en dernier par défaut.

### URLs par source

| Clé | Source |
| --- | --- |
| `wawacity_base_url` | Wawacity (obligatoire pour scanner) |
| `wawacity_user_agent` | User-Agent optionnel |
| `free_telecharger_user_agent` | Free-Télécharger (URL via fiche fournisseur en base) |
| `torrentio_base_url` | Torrentio (défaut public dans l’image) |
| `comet_base_urls` | Liste d’instances Comet (une par ligne) ; `comet_base_url` garde la première |
| `stremio_addons` | JSON ou liste d’addons Stremio |

Migration de domaine Wawacity : bannière et actions **Accepter / Rétablir** si une redirection est proposée.

### Addons Stremio

Source `stremio_addons` : liste ordonnée `{ url, enabled }`, même ergonomie que Comet.

- Une requête stream par addon activé ; cache et délai partagés avec les autres sources.
- Segments d’URL contenant clés débrid ou tracker **retirés** à la saisie.
- Flux informatifs ou lecteurs seuls ignorés ; sélection selon [Profils de qualité](#docs-reglages-qualite).

### Test de source

Bloc **Tester les sources** :

- **Titre** obligatoire (film, ou épisode avec suffixe `S01E02`). Année optionnelle entre parenthèses.
- **Identifiant TMDB** optionnel pour enrichir la sonde sans modifier la base.
- **Tester** (carte) ou **Tester toutes** : une requête témoin par source (ou une par addon Stremio activé) ; **aucun téléchargement**.
- Résultats : messages du moteur, durée, profil utilisé ; dernier test mémorisé par carte.

### Hive

Si Hive est activé et que vous appartenez à des ruches, l’interface peut afficher le nombre de ruches.

Voir [Chaîne de téléchargement](#docs-telechargements) et [Hive](#docs-hive).

## Réglages de scan

Chemin source : `docs/reglages/scan.md`

<a id="docs-reglages-scan"></a>

URL : `/reglages/scan`

### Scan des sources (séries / films suivis)

| Clé | Défaut | Plage | Rôle |
| --- | --- | --- | --- |
| `scan_interval_min` | 720 | 60–10080 | Intervalle entre scans (minutes) |
| `scan_ongoing_only` | true | bool | Ne repoller que les séries encore en cours |
| `scan_air_window_days` | 3 | 0–30 | Fenêtre autour de la date de diffusion (séries) |
| `film_theatrical_fallback_months` | 4 | 0–24 | Mois après sortie salle si TMDB sans date numérique |

L’ordre des sources se règle dans [Sources](#docs-reglages-sources).

Sans téléchargement disponible (AllDebrid absent et Hive inutilisable), l’option **Séries en cours seulement** et le bloc **Téléchargement automatique pour les nouveaux suivis** sont masqués avec un renvoi vers [Services et jetons](#docs-reglages-jetons).

### Chemins (absolus dans le conteneur)

| Clé | Défaut si vide (`DATA_DIR=/data`) |
| --- | --- |
| `media_dir` | `/data/media` |
| `library_movies_path` | `/data/media/Films` (surcharge env `MOVIES_DIR`) |
| `library_series_path` | `/data/media/Series` (surcharge env `SERIES_DIR`) |
| `downloads_dir` | `/data/downloads` |

Le dossier corbeille (`trash_dir`) se règle dans [Corbeille](#docs-reglages-corbeille).

L’UI affiche les chemins **en vigueur** et un **warning** si les volumes ne permettent pas les renommages atomiques.

### Bibliothèque disque

| Clé | Défaut | Rôle |
| --- | --- | --- |
| `library_scan_interval_hours` | 24 | Repasse planifiée (1–168 h) |
| `library_scan_enabled` | true | Active la repasse automatique |
| `library_auto_follow` | false | Suivre automatiquement après identification TMDB |
| `auto_download_default` | true | Téléchargement auto pour les nouveaux suivis (masqué sans source de téléchargement) |

Une repasse a aussi lieu après chaque fichier déposé. L’action après téléchargement terminé (`downloads_completed_action`) se règle dans [Corbeille](#docs-reglages-corbeille).

### Bibliothèque (onglets)

Écran **Bibliothèque** (`/bibliotheque`) : trois onglets (compteurs dans l’interface).

| Onglet | Rôle |
| --- | --- |
| **À traiter** | Dossiers à ranger (films / séries), fichiers suspects, regroupements, alignement Jellyfin si connecté, renommages en attente |
| **Nettoyage** | Dossiers vides, téléchargements incomplets (aperçu avant envoi à la corbeille si l’option est activée dans [Corbeille](#docs-reglages-corbeille)) |
| **Contenu** | Films et séries déjà conformes, avec filtres et tri |

L’URL peut mémoriser l’onglet (`?onglet=nettoyage`, etc.). Le paramètre `incomplets=corbeille` ouvre directement *Nettoyage*.

## Jellyfin

Chemin source : `docs/reglages/jellyfin.md`

<a id="docs-reglages-jellyfin"></a>

URL : `/reglages/jellyfin`

**URL** et **clé API** se renseignent sur **Réglages → Services et jetons**, carte Jellyfin. Tant que Jellyfin n’est pas « connecté » (URL + clé enregistrées), cette page n’affiche que le renvoi vers la carte.

Guide détaillé (webhook, chemins Docker, dépannage) : [../jellyfin.md](#docs-jellyfin).

### Contenu de la page (Jellyfin connecté)

- **Utilisateur Jellyfin** : compte dont l’état « vu » est synchronisé avec MediaTracker (liste chargée depuis le serveur).
- **Correspondance des chemins** (`jellyfin_path_map`) : paires `préfixe_côté_Jellyfin=chemin_MediaTracker`, séparées par `;`.
- **Ancien préfixe** (`jellyfin_path_prefix`) : repli si la correspondance ne couvre pas un chemin.
- **Renommage automatique** (`jellyfin_auto_rename`) : désactivé par défaut.
- **Pousser un vu manuel** (`jellyfin_push_watched`) : désactivé par défaut ; un vu posé à la main n’est pas effacé par la synchro entrante.
- **Synchro « vu » (minutes)** (`jellyfin_poll_interval_min`) : passage périodique ; le **webhook** met à jour plus vite s’il est configuré.
- **Webhook** : URL et jeton affichés pour le plugin Jellyfin (en-tête `X-MediaTracker-Token` prioritaire sur `?token=`).
- Boutons **Tester la connexion** et **Synchroniser les vus**.

Après chaque fichier déposé, MediaTracker demande aussi un rafraîchissement des bibliothèques Jellyfin (environ une minute).

### Import d’historique « vu »

L’import hors ligne (copie de base Jellyfin ou CSV) ne figure plus dans le menu principal **Bibliothèque**. Il est regroupé avec la configuration Jellyfin :

1. Renseigner URL et clé sur **Services et jetons**, carte Jellyfin.
2. Sur la même carte, **Importer l’historique** ouvre l’écran d’import (route dédiée sous `/bibliotheque/…`).

Deux modes sur cet écran : **Bases Jellyfin** (recommandé) et **Fichier CSV**. Le suivi reste désactivé tant que vous ne le réactivez pas après l’import.

Étapes, formats et limites : section *Importer l’historique « vu » depuis Jellyfin* dans [../jellyfin.md](#docs-jellyfin).

### Bibliothèque

Avec Jellyfin connecté, l’onglet **À traiter** de la **Bibliothèque** peut proposer l’**alignement Jellyfin** (fusion ou rattachement de fiches selon l’état vu côté serveur). Voir [scan.md](#docs-reglages-scan).

## Notifications

Chemin source : `docs/reglages/notifications.md`

<a id="docs-reglages-notifications"></a>

URL : `/reglages/notifications`

| Clé | Rôle |
| --- | --- |
| `public_base_url` | Lien vers la fiche dans les messages (souvent URL LAN) |
| `notify_group_seconds` | Regroupe les fin de téléchargement (0 = désactivé) |
| `notify_quiet_start` / `notify_quiet_end` | Heures calmes `HH:MM`, fuseau `notify_timezone` (défaut Europe/Paris) |

Cibles : **Home Assistant** (webhook sans HMAC), **ntfy**, **webhook signé** HMAC-SHA256. Événements sélectionnables par cible (nouveautés décoché par défaut à la création).

## Corbeille

Chemin source : `docs/reglages/corbeille.md`

<a id="docs-reglages-corbeille"></a>

URL : `/reglages/corbeille`. L’URL `/reglages/nettoyage` redirige vers cette page.

Section **Stockage** : contenu de la corbeille, rétention, suppression automatique du vu, incomplets, conservation des sauvegardes et de la liste des téléchargements, renvoi vers la purge AllDebrid si le compte est connecté.

### Contenu

Liste des fichiers en corbeille : **Restaurer**, **Supprimer** (définitif), **Vider la corbeille** (avec confirmation).

### Durée en corbeille

| Clé | Défaut | Plage | Rôle |
| --- | --- | --- | --- |
| `trash_retention_days` | 7 | 1–90 | Jours avant purge définitive des fichiers en corbeille |
| `trash_dir` | `/data/.trash` | chemin | Dossier corbeille (même volume que les bibliothèques) |

L’interface affiche le chemin **en vigueur** et un avertissement si les volumes empêchent les renommages atomiques.

### Suppression automatique du vu

| Clé | Défaut | Plage | Rôle |
| --- | --- | --- | --- |
| `auto_delete_enabled` | true | bool | Suppression auto du contenu vu |
| `auto_delete_days` | 14 | 1–365 | Délai après visionnage |
| `cleanup_notice_hours` | 24 | 1–168 | Préavis notification |

Comportement affiché : le contenu vu part à la corbeille après le délai et le préavis ; le suivi n’est pas requis ; une saison n’est retirée que si elle est entièrement vue. Une fiche peut imposer son propre délai ou être marquée **Garder**.

Bouton **Aperçu du prochain passage** : listes *Sera supprimé* et *Annoncé, pas encore supprimé*.

### Téléchargements incomplets

| Clé | Défaut | Rôle |
| --- | --- | --- |
| `incomplete_cleanup_enabled` | false | Après aperçu dans **Bibliothèque** (onglet *Nettoyage*) |

Quand c’est activé : orphelins déjà en bibliothèque, fichiers minuscules et contrôles aria2 peuvent partir à la corbeille ; les autres incomplets restent le nombre de jours indiqué dans **Durées fixes** ci-dessous.

### Conservation

| Clé | Défaut | Plage | Rôle |
| --- | --- | --- | --- |
| `backup_keep` | 7 | 1–90 | Sauvegardes SQLite quotidiennes conservées (`/config/backups`) |
| `downloads_completed_action` | `archive` | `archive` / `remove` | Entrée dans la liste Téléchargements après dépôt (fichier conservé en bibliothèque) |

### Durées fixes

Conservation automatique **côté serveur**, affichée en lecture seule dans le bloc **Durées fixes** (non modifiable dans l’interface). API : `GET /api/v1/settings/fixed-retention`.

| Élément (libellé UI) | Rôle |
| --- | --- |
| Journal des événements | Purge des entrées du journal au-delà de la fenêtre affichée |
| Historique des états des sources | Purge des changements d’état Wawacity, Comet, hébergeurs, etc. |
| Métriques de performance | Purge des points d’historique CPU/RSS |
| Téléchargements incomplets (avant nettoyage) | Délai avant proposition de corbeille pour un incomplet sans téléchargement actif |

Les valeurs exactes (en jours) sont celles affichées sur la page.

### AllDebrid

Bloc **AllDebrid** : lien vers [AllDebrid](#docs-reglages-alldebrid) pour la purge manuelle — **uniquement si le compte AllDebrid est connecté** (sinon bloc absent).

## AllDebrid

Chemin source : `docs/reglages/alldebrid.md`

<a id="docs-reglages-alldebrid"></a>

URL : `/reglages/alldebrid`

La **clé API** ou la liaison **PIN** se configurent sur **Réglages → Services et jetons**, carte AllDebrid.

Tant qu’AllDebrid n’est pas connecté, cette page n’affiche que le renvoi vers la carte.

### Réglages (compte connecté)

- **`alldebrid_auto_purge`** : après un rangement réussi, retire le lien enregistré, l’entrée d’historique correspondante et l’aimant du téléchargement sur le compte (activé par défaut).
- **Purger AllDebrid** : ne retire que les liens des téléchargements terminés par MediaTracker.
- **Tout purger** : confirmation requise — vide l’historique récent du compte, y compris les aimants que MediaTracker n’a pas créés.

La clé peut être préfixée via env `ALLDEBRID_API_KEY` au premier démarrage.

Voir aussi le bloc **AllDebrid** dans [Corbeille](#docs-reglages-corbeille) (lien rapide vers cette page).

## Disque et sauvegardes

Chemin source : `docs/reglages/disque.md`

<a id="docs-reglages-disque"></a>

URL : `/reglages/disque`

### Espace disque

Carte **espace disque** (volumes données et configuration).

| Clé | Défaut | Plage | Rôle |
| --- | --- | --- | --- |
| `disk_min_free_gb` | 5 | 0–1024 | Garde-fou espace libre sur le volume des téléchargements (`0` = désactivé) |

Comportement :

- **Sous le seuil** : téléchargements en **pause**, notification « Espace disque » ; les jobs actifs passent en erreur `DISK_LOW` jusqu’au retour au-dessus du seuil.
- **Avant de lancer un nouveau téléchargement** (aria2) : le serveur exige l’espace libre **minimum réglé**, plus la taille estimée du fichier (avec une marge fixe), sinon le job reste en attente (`DISK_FULL`) sans démarrer aria2.
- Quand l’espace redevient suffisant, les téléchargements en pause pour cause de disque sont repris.

Le nombre de sauvegardes quotidiennes conservées se règle dans [Corbeille](#docs-reglages-corbeille) (`backup_keep`).

### Sauvegarde / restauration

Bloc **Sauvegarde / restauration** : liste des copies dans `/config/backups`, téléchargement d’un fichier `.db`, import avec aperçu et confirmation.

Voir [Sauvegarde](#docs-sauvegarde).

## Hive

Chemin source : `docs/reglages/hive.md`

<a id="docs-reglages-hive"></a>

URL : `/reglages/hive`

Page composant dédié (pas dans `SettingsSectionView`).

Réglages persistés : `hive_enabled`, `hive_public_address`, `hive_instance_name` (via activation), `hive_catalog_auto_share`, `hive_request_on_miss`, `hive_transfer_parallel`, `hive_transfer_send_bps`, `hive_transfer_recv_bps`, `hive_transfer_window_start`, `hive_transfer_window_end`.

Guide utilisateur : [Hive](#docs-hive).

## Application

Chemin source : `docs/reglages/application.md`

<a id="docs-reglages-application"></a>

URL : `/reglages/application`

Propose l’**installation PWA** (Progressive Web App) lorsque le navigateur expose l’événement `beforeinstallprompt`. Hors ligne partiel : file d’ids TMDB selon l’implémentation PWA du frontend.

## Mise à jour et redémarrage

Chemin source : `docs/reglages/maj.md`

<a id="docs-reglages-maj"></a>

URL : `/reglages/maj`

- **Version installée** : version, image, date de build, dernière vérification.
- **Vérifier la mise à jour** : comparaison registre / GitHub (jetons sur [Services et jetons](#docs-reglages-jetons), carte GitHub).
- **Mettre à jour maintenant** : délègue à Watchtower si URL et jeton Watchtower sont configurés (même carte **Services et jetons**).
- **Redémarrer** : redémarrage applicatif.
- **Me notifier quand Watchtower installe une nouvelle version** (`notify_on_update`) : notification via les canaux définis dans [Notifications](#docs-reglages-notifications) — interrupteur masqué si Watchtower n’est pas configuré.

Voir [Mises à jour](#docs-mises-a-jour).

## Services et jetons

Chemin source : `docs/reglages/jetons.md`

<a id="docs-reglages-jetons"></a>

URL : `/reglages/jetons`

Un **service par carte**. Les secrets sortants (TMDB, AllDebrid, Jellyfin, GitHub, Watchtower) ne sont **jamais réaffichés** après enregistrement (chiffrement en base). Pour Watchtower, utilisez le jeton du conteneur (`WATCHTOWER_HTTP_API_TOKEN`), pas un jeton **Accès API** MediaTracker.

### TMDB

- **Jeton de lecture** (`tmdb_read_token`).
- **Déverrouille** : écrans Découvrir et Nouveautés, identification TMDB des médias et de la bibliothèque disque.
- Sans jeton : pas de recherche catalogue ni d’identification distante TMDB.

### AllDebrid

- Connexion via **clé API** ou flux **PIN** (bloc compte sur la carte).
- **Déverrouille** :
  - page [Sources](#docs-reglages-sources) : ordre des sources, cartes Wawacity / Free-Télécharger / Comet / Torrentio / addons Stremio, **rythme des requêtes** et **ordre des hébergeurs** ;
  - page [AllDebrid](#docs-reglages-alldebrid) : purge automatique après dépôt, purges manuelles ;
  - dans [Réglages de scan](#docs-reglages-scan) : options de téléchargement automatique des nouveaux suivis.
- **Masqué sans clé AllDebrid connectée** : les sources qui passent par AllDebrid n’apparaissent pas dans l’ordre ni dans les réglages détaillés ; un message oriente vers cette carte ou vers [Hive](#docs-reglages-hive). **Hive** reste visible si Hive est activé.
- Sans AllDebrid **ni** Hive utilisable, le téléchargement depuis les sources est bloqué (`download_ready` faux).

### Jellyfin

- **URL** et **clé API** (`jellyfin_url`, `jellyfin_api_key`).
- **Déverrouille** :
  - section [Jellyfin](#docs-reglages-jellyfin) : utilisateur, correspondance des chemins, webhook, synchro des vus, tests ;
  - bloc *Alignement Jellyfin* dans la **Bibliothèque** (onglet *À traiter*) ;
  - bouton **Importer l’historique** sur la carte (parcours détaillé dans [jellyfin.md](#docs-reglages-jellyfin) et [../jellyfin.md](#docs-jellyfin)).
- Tant que l’URL ou la clé manquent, la section Jellyfin n’affiche que le renvoi vers cette carte.

### GitHub

- **Jeton GHCR (lecture)** (`ghcr_read_token`) : métadonnées de l’image pour comparer les versions.
- **Jeton GitHub (repli)** (`github_update_token`) : dernier commit sur la branche suivie si GHCR est indisponible.
- **Déverrouille** : comparaison de version et badge « nouvelle image » dans [Mise à jour](#docs-reglages-maj).

### Watchtower

- **URL** (`watchtower_url`) et **jeton** (`watchtower_token`), alignés sur l’API HTTP de Watchtower.
- **Déverrouille** : bouton **Mettre à jour maintenant** et interrupteur de notification après mise à jour automatique dans [Mise à jour](#docs-reglages-maj).
- Sans URL et jeton valides, la mise à jour immédiate reste indisponible (message dans l’interface).

### Accès API (entrant)

Jetons `mt_…` **créés dans MediaTracker** pour scripts et intégrations sur le LAN (Home Assistant, etc.).

- Portées : `read`, `write`, `admin`.
- Le secret n’est montré **qu’une fois** à la création.
- Ne sert **pas** pour Watchtower ni pour Jellyfin.

## Réinitialiser la base

Chemin source : `docs/reglages/reset.md`

<a id="docs-reglages-reset"></a>

URL : `/reglages/reset`

Efface fiches, téléchargements et index bibliothèque. Une copie SQLite est écrite dans `/config/backups` avant suppression. Les fichiers sur `/data` **ne sont pas** supprimés.

Confirmation textuelle : saisir `RESET`.

## Médias

Chemin source : `docs/medias.md`

<a id="docs-medias"></a>

**Médias** (`/medias`) est l’écran qui réunit toutes vos **fiches** films et séries suivies ou conservées dans MediaTracker : affiches, état de téléchargement, progression de visionnage et raccourcis vers la fiche détaillée ou Jellyfin.

Pour le jeton TMDB, le suivi depuis **Suivre** et la correction d’identifiant, voir [TMDB](#docs-tmdb). Pour le partage entre instances, voir [Hive](#docs-hive).

### À la une

En haut de la page, un bandeau **À la une** met en avant une fiche parmi celles **actuellement affichées** (filtres et recherche pris en compte) :

- priorité à une **série en cours** (épisodes vus, mais pas tous) ;
- sinon la première carte de la liste filtrée.

Le bandeau propose **Fiche** (vers `/medias/<id>`) et, si Jellyfin est lié, **Lire sur Jellyfin**.

### Chargement de la liste

La grille charge l’intégralité de votre collection par **pages** via `GET /api/v1/media` (200 fiches par page, curseur de pagination). Le compteur **Chargement… N titres reçus** augmente au fur et à mesure.

- Hors ligne (mode PWA), l’application peut afficher la **dernière visite** en cache ; les affiches ne sont pas conservées hors ligne.
- Si **Hive** est activé, des pastilles Hive sur les cartes se chargent **après** la grille, sans bloquer l’affichage des affiches.

Les statistiques sous le titre indiquent le nombre total de titres, de séries et de films. Lorsqu’un filtre ou une recherche est actif, une ligne **N affichés** précise le sous-ensemble visible.

### Filtres d’état (boutons)

Une rangée de boutons restreint la liste côté interface (sans nouvel appel API) :

| Bouton | Effet |
| --- | --- |
| **Tous** | Aucun filtre d’état |
| **Disponibles** | Fiches dont la disponibilité est « disponible » (fichier obtenu via le suivi) |
| **En attente** | En attente de téléchargement, pas encore vues |
| **Sur le disque** | Fichier présent sur le NAS (y compris partiel pour une série) |
| **En pause** | Suivi en pause |
| **Suivi par Jellyfin** | Fiches issues de l’identification bibliothèque (`follow_origin` identification) |

Sur **Suivi par Jellyfin**, l’action **Ne plus suivre ces fiches** appelle `POST /api/v1/media/unfollow-identified` : les fiches identifiées passent en non suivies, les fichiers restent en place.

### Recherche, filtres avancés et tri

**Recherche** : filtre instantané sur le **titre** (insensible à la casse, locale française).

**Tri** :

- **Alphabétique** (défaut) ;
- **Récents** (date d’ajout décroissante).

**Filtres avancés** (menus **Suivi**, **Vu**, **Type**, **Origine**) :

| Filtre | Valeurs |
| --- | --- |
| Suivi | Tous, Suivis, Non suivis |
| Vu | Tous, Vus, En cours (progression > 0 sans « vu » global), Non vus |
| Type | Tous, Séries, Films |
| Origine | Toutes, Ajout manuel, Import Jellyfin, Identifiés bibliothèque, À identifier TMDB |

Sur mobile, le panneau **Filtres** regroupe ces réglages ; des **pastilles** rappellent les filtres actifs, avec **Réinitialiser** pour tout effacer.

### Densité de la grille

**Aérée** ou **Dense** : le choix est mémorisé dans le navigateur. En mode dense, l’année et le type sur chaque carte sont allégés.

### Cartes et pastilles

Chaque carte affiche l’affiche TMDB (cache local), le titre, le type (Film / Série), l’année (sauf en dense), puis :

**Pastille d’état** (priorité dans cet ordre) :

| Pastille | Signification |
| --- | --- |
| En pause | Suivi suspendu |
| Vu | Marqué comme vu |
| Sur le disque | Fichier présent |
| Partiel | Série incomplète sur disque (libellé personnalisé possible) |
| Disponible | Fichier obtenu via le suivi |
| Pas encore disponible | Aucune source trouvée pour l’instant |
| En attente | En attente de téléchargement |

**Icônes** complémentaires : **Suivi**, **Sur le disque**.

**Pastilles Hive** (si Hive actif, sur les fiches visibles) : par exemple **Partagé**, **Préparation au partage**, **En transfert**, **Mode tirage**, etc.

Un clic sur la carte ouvre l’**aperçu rapide** (voir ci-dessous). Le lien **Fiche** sur la carte mène à `/medias/<id>`.

### Menu de carte

Chaque carte expose un menu d’actions :

- **Appui long** (tactile) ou **clic droit** sur l’affiche ;
- touche **Menu** au clavier (indication d’accessibilité sur la carte).

Entrées typiques sur la liste Médias :

- **Fiche** ;
- **Lire sur Jellyfin** (si une URL Jellyfin est connue).

En mode **Sélectionner**, le lien **Fiche** sur la carte est masqué ; un clic sur la carte coche ou décoche la fiche.

### Aperçu rapide

Un clic sur l’affiche (hors sélection multiple) ouvre un panneau **aperçu** :

- synopsis, année, type ;
- pour une fiche **non suivie** : **Suivre** (avec choix des saisons et politique des nouveaux épisodes pour une série), lien **TMDB**, **Fiche** ;
- pour une fiche **suivie** : **Fiche**, **Lire sur Jellyfin**, **Source** (si applicable), **Fermer**.

Données complétées via `GET /api/v1/media/<id>` lorsque l’aperçu s’ouvre.

**Échap** ferme l’aperçu (ou le sous-dialogue de suivi).

### Fiche média (`/medias/<id>`)

La fiche charge `GET /api/v1/media/<id>` et, pour une série, `GET /api/v1/media/<id>/items` (saisons et épisodes).

#### En-tête commun

- Affiche, titre, année, synopsis ;
- pour une série : statut TMDB, nombre de saisons, barre **X vu sur Y**, prochain épisode ;
- pastilles **Suivi**, état fichier ou **Pas encore disponible** (film) ;
- liens **TMDB** / **Jellyfin**, **Bande-annonce** (si TMDB fournit une clé YouTube).

#### Actions principales (fiche suivie)

Le bloc **Suivi** / **Actions** (composant partagé film et série) regroupe :

- **Action principale** selon l’état : *Vérifier maintenant*, *Rechercher maintenant*, *Retélécharger*, *Restaurer* (fichier en corbeille), etc. ;
- **Mettre en pause** / **Reprendre** ;
- menu **Plus d’actions** : vérification manuelle, pour une série *Marquer la série comme vue / non vue*, **Ne plus suivre…** ;
- interrupteurs **Garder** (film ou série), **Téléchargement automatique** (série) ;
- section **État** repliable : progression, fichier, dernière et prochaine vérification, message du moteur de recherche.

Fiche **série** : mise en page dédiée (carte Suivi mobile, barre d’actions bureau).

#### Fiche film — fichier

Hors « pas encore disponible », le panneau **Film** détaille le fichier unique : statut, nom, actions **Garder**, marquer vu, retélécharger, forcer le téléchargement, suppression vers corbeille, partage Hive si applicable.

#### Fiche série — saisons et épisodes

Section **Saisons et épisodes** (série suivie ou fiches avec fichiers restants) :

- chaque saison se **déplie** ; compteurs vus, fichiers sur disque, épisodes au catalogue ;
- par saison : **Garder**, menu (marquer saison vue/non vue, supprimer la saison, forcer la saison, partage Hive en masse) ;
- par épisode : code SxxExx, statut (téléchargé, en cours, absent…), nom de fichier, date de diffusion, puce Hive ;
- **Garder** par épisode (désactivé si la série ou la saison est déjà en « Garder ») ;
- menu épisode : vu/non vu, vu jusqu’à cet épisode, restaurer, retélécharger, télécharger, forcer, supprimer, partage Hive, etc.

#### Réglages de la fiche

Le repli **Réglages de cette fiche** (`?reglages=ouverts` dans l’URL l’ouvre) contient notamment : profil de qualité et repli, ordre des sources, identification TMDB, association dossier bibliothèque, Jellyfin (séries), maintenance (liens morts, réparation). Voir [Profils de qualité](#docs-reglages-qualite) et [TMDB — Corriger l’identifiant](#docs-tmdb-corriger-lidentifiant-tmdb-dune-serie).

#### Fiche non suivie

Bandeau **Suivre à nouveau** à la place des actions de suivi actif. Les opérations avancées sont sous **Plus d’actions** (garder, réglages, TMDB, changement d’identifiant, suppression fichiers ou fiche). La zone **Suppression et fin du suivi** en bas n’apparaît pas sur l’écran épuré non suivi — détail dans [TMDB — Fiche non suivie](#docs-tmdb-fiche-non-suivie).

#### Zone danger (fiche suivie)

Repli **Suppression et fin du suivi** :

- **Ne plus suivre** (option corbeille pour les fichiers) ;
- **Supprimer tous les fichiers** / **le film** (corbeille, fiche conservée) ;
- **Supprimer la fiche…** : dialogue avec choix **corbeille** ou **conserver sur le disque** (`DELETE /api/v1/media/<id>?trash=true|false`).

### Sélection multiple et suppression en masse

Sur **Médias** (`/medias`), le bouton **Sélectionner** permet de cocher plusieurs fiches **parmi celles affichées** (filtres et recherche respectés), puis **Supprimer les fiches**.

- Barre d’outils : compteur, **Tout sélectionner** (visible uniquement), **Annuler**, **Supprimer les fiches** ;
- **Échap** quitte le mode sélection ou ferme le dialogue de confirmation ;
- la confirmation appelle `DELETE /api/v1/media/bulk` avec `trash: false` : les fiches, épisodes, liens et historique associés sont retirés ; les téléchargements en cours sont **annulés** ; le journal des renommages est **conservé** ;
- en sélection multiple, l’interface précise que **les fichiers sur le disque ne sont pas touchés** (aucun envoi à la corbeille). Les dossiers restent dans la bibliothèque physique ;
- depuis **une seule fiche**, le dialogue de suppression propose le choix entre corbeille et conservation sur disque.

Le mode sélection est indisponible hors ligne lorsque les actions serveur sont désactivées.

## TMDB

Chemin source : `docs/tmdb.md`

<a id="docs-tmdb"></a>

**TMDB** (*The Movie Database*) est un catalogue en ligne de films et séries. MediaTracker s’en sert en **lecture seule** pour afficher les affiches, les métadonnées et pour identifier vos fichiers sur le disque.

### Jeton API

MediaTracker appelle l’API TMDB v4 avec un **jeton de lecture** (`tmdb_read_token`) :

1. Créez un compte sur [themoviedb.org](https://www.themoviedb.org/settings/api).
2. Générez un jeton API en mode **lecture** (Read Access Token).
3. Saisissez-le dans l’assistant **TMDB** au premier démarrage, ou plus tard dans **Réglages**.

Le jeton est **chiffré** en base de données. Vous pouvez aussi le passer une fois en variable d’environnement au tout premier boot : `TMDB_READ_TOKEN` (voir [`.env.example`](../.env.example)).

### Dans l’application

| Écran | Rôle |
| --- | --- |
| **Accueil** (`/`) | Tableau de bord personnel et rangées de nouveautés TMDB (France, français) |
| **Derniers films** (`/nouveautes/films`) | Parcourir les films (cinéma, streaming, supports physiques) |
| **Dernières séries** (`/nouveautes/series`) | Séries et diffusions à l’antenne |
| **Suivre** | Rechercher un titre et l’ajouter à votre liste de suivi |
| **Médias** (`/medias`) | Liste de vos fiches — voir [medias.md](#docs-medias) |
| Fiche média (`/medias/<id>`) | Métadonnées, suivi, saisons et épisodes — voir [medias.md](#docs-medias) |

Les anciennes URL d’essai `/tmdb`, `/tmdb/films` et `/tmdb/series` redirigent vers les écrans ci-dessus.

### Fiche non suivie

Lorsque vous avez arrêté le suivi d’un titre mais que la fiche reste en base (fichiers sur le disque, historique, etc.) :

- Le bandeau propose **Suivre à nouveau** pour réactiver le suivi sans repasser par **Suivre**.
- Les actions avancées (nettoyage, suppression de fichiers, etc.) sont regroupées sous **Plus d’actions** ; la zone de suppression définitive de la fiche n’apparaît pas sur cet écran épuré.
- Le partage **Hive** reste accessible dans le même bloc que pour une fiche suivie, avec l’état adapté (fichier présent ou non).

### Profils de qualité

Les critères de résolution, langue et mots à exclure pour **choisir un fichier** parmi les liens d’une source se règlent dans **Réglages → Profils de qualité** — voir [reglages/qualite.md](#docs-reglages-qualite).

### Corriger l’identifiant TMDB d’une série

Chaque fiche média est liée à un **identifiant numérique TMDB**. Si vous avez suivi la mauvaise série (homonyme, mauvaise saisie), vous pouvez le corriger depuis la fiche.

#### Première saisie

Lorsque la fiche n’avait **pas encore** d’identifiant TMDB, la nouvelle valeur est enregistrée immédiatement.

#### Remplacement sur une série qui en avait déjà un

Le serveur affiche d’abord un **aperçu** des changements (saisons, épisodes, fichiers concernés). Rien n’est modifié tant que vous n’avez pas confirmé.

**Où aller :** fiche média → **Réglages** → **Identification** → **Corriger l’identifiant**.

**Trois choix possibles :**

| Action | Effet |
| --- | --- |
| **Reconstruire les épisodes** | Les lignes d’épisodes de l’ancien catalogue qui n’existent plus sous le nouvel identifiant sont retirées de la fiche. Les **fichiers sur le disque ne sont ni supprimés ni déplacés** ; ils apparaissent dans la bibliothèque en zone **À réviser**, avec la note « À réassocier », jusqu’à ce que vous les rattachez manuellement à un épisode. |
| **Conserver les épisodes** | Comportement plus permissif : les épisodes hors nouveau catalogue TMDB restent listés sur la fiche. |
| **Annuler** | Aucune modification. |

**À savoir :**

- Un simple **rafraîchissement** des métadonnées TMDB (même identifiant) ne déclenche pas ce flux.
- Les fichiers d’un autre titre portant le même numéro de saison/épisode (S01E05, etc.) **ne sont pas** rattachés automatiquement au nouveau catalogue.
- Tant qu’un fichier porte la note « À réassocier », le scan bibliothèque **ne le rattache pas** tout seul.

Pour un **film**, le changement d’identifiant reste une opération directe (pas d’aperçu 409).

## Jellyfin

Chemin source : `docs/jellyfin.md`

<a id="docs-jellyfin"></a>

**Jellyfin** est un serveur multimédia qui lit les fichiers vidéo de votre bibliothèque. MediaTracker peut y **déposer** les téléchargements terminés, **rafraîchir** les bibliothèques et synchroniser l’état **vu / non vu** dans les deux sens.

### Configuration

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

### Webhook Jellyfin → MediaTracker

L’interface affiche l’URL du webhook et un jeton (en-tête `X-MediaTracker-Token`, prioritaire sur `?token=`). Événements pris en charge : PlaybackStop, UserDataSaved, ItemAdded, ItemUpdated.

Après chaque fichier déposé, MediaTracker demande aussi un **rafraîchissement** de la bibliothèque Jellyfin (délai d’environ une minute).

### Bibliothèque locale

- **Bibliothèque** : scan des dossiers films / séries, identification TMDB.
- **Suivre automatiquement après identification** : réglage dans **Scan** (`library_auto_follow`, désactivé par défaut).
- **Repasse** : intervalle en heures, plus une repasse après chaque dépôt.

### Importer l’historique « vu » depuis Jellyfin

MediaTracker peut reprendre ce que vous aviez déjà marqué comme **vu** dans Jellyfin, sans rester connecté au serveur Jellyfin pendant l’import.

**Entrée dans l’application** : **Réglages → Services et jetons**, carte Jellyfin — bouton **Importer l’historique** (après URL et clé API). Voir aussi [Réglages — Jellyfin](#docs-reglages-jellyfin).

Deux modes sur l’écran d’import :

- **Bases Jellyfin** (recommandé) ;
- **Fichier CSV** (export préalable ou archivage).

#### Avant de copier des fichiers Jellyfin

Sur le serveur Jellyfin (ou une sauvegarde arrêtée), les données utiles se trouvent en général dans le répertoire de données Jellyfin, sous-dossier `data/` :

| Fichier | Rôle |
| --- | --- |
| `jellyfin.db` | Base principale (comptes, bibliothèque, marquages vus, journal d’activité) |
| Archive `.zip` (tableau de bord → Sauvegardes) | Export Jellyfin 10.11 avec fichiers JSON (`Users`, `UserData`, `BaseItems`, `ActivityLogs`, …) |

**Conseil :** arrêtez le conteneur Jellyfin ou travaillez sur une **copie** des fichiers. Les outils MediaTracker ouvrent les bases en **lecture seule**.

#### Parcours guidé (onglet Bases Jellyfin)

1. **Importer l’historique** (depuis la carte Jellyfin des réglages) → mode *Bases Jellyfin*.
2. Envoyez une copie de **jellyfin.db** **ou** une archive **.zip** de sauvegarde Jellyfin 10.11.
3. Choisissez le **compte Jellyfin** à importer.
4. **Prévisualisation** : compteurs par source (bibliothèque, journal d’activité, éléments sans titre) et compteurs d’identification dans MediaTracker. Vous pouvez **télécharger le CSV** généré pour vos archives.
5. **Confirmer** : marquage « vu » avec la date d’origine ; création éventuelle de fiches **sans suivi automatique**.
6. **Réactiver le suivi** : case à cocher par titre du dernier import, puis **Appliquer le suivi**.

Les fichiers envoyés sont stockés temporairement dans le dossier de configuration MediaTracker, vérifiés (SQLite ou ZIP), puis **supprimés** après confirmation, abandon ou erreur. Les restes de plus de 24 h sont nettoyés au démarrage. Taille maximale par fichier : **1 Go** par défaut (réglage `JELLYFIN_HISTORY_UPLOAD_MAX_BYTES`).

Derrière un **proxy inverse**, augmentez la limite de taille des requêtes si besoin (par exemple `client_max_body_size` côté nginx).

#### Export CSV en ligne de commande (optionnel)

Depuis une machine avec Python 3, à la racine du dépôt :

```bash
python3 tools/jellyfin_history_export.py \
  --jellyfin-db /chemin/vers/copie/jellyfin.db \
  --user NomDuCompte \
  --out historique-jellyfin.csv
```

`--user` est facultatif s’il n’y a qu’un seul compte. Le parcours guidé dans l’interface s’appuie surtout sur le **journal d’activité** Jellyfin ; l’export CLI lit directement `jellyfin.db`.

Colonnes du CSV (UTF-8) : `type`, `title`, `year`, `series_title`, `season`, `episode`, identifiants externes (`tmdb_id`, etc.), `jellyfin_item_id`, `watched_at`, `play_count`, `source`.

#### Import CSV (onglet secondaire)

1. **Importer l’historique** → mode *Fichier CSV*.
2. **Prévisualisation** puis **Confirmer** : même logique idempotente (réimporter le même fichier ne crée pas de doublons).
3. **Réactiver le suivi** : liste à cocher du **dernier** import uniquement.

Chaque import confirmé ajoute **une** entrée dans le journal applicatif, pas une par ligne.

### Dépannage

- L’écran **Santé** signale les dossiers sur des volumes différents ou des chemins incohérents.
- Boutons **Tester la connexion** et **Synchroniser les vus** dans **Réglages → Jellyfin**.

## Hive

Chemin source : `docs/hive.md`

<a id="docs-hive"></a>

**Hive** est le mode de **partage de catalogue** et de **téléchargement entre instances** MediaTracker qui appartiennent aux mêmes **ruches** (groupes de membres approuvés).

### Activation

**Réglages → Hive** (`/reglages/hive`) :

1. Activer l’interrupteur **Hive** (désactivé par défaut).
2. À la première activation, choisir un **nom d’instance** (lié à l’identité cryptographique ; ne change pas sans réinitialisation d’identité).
3. Renseigner l’**adresse publique** HTTPS (ou HTTP) joignable par les autres instances — par exemple `https://mtr.exemple.fr` — **sans** identifiants dans l’URL.

L’entrée de menu **Hive** n’apparaît que lorsque Hive est activé.

### Ruches et membres

- **Créer une ruche** : depuis Réglages Hive ou la navigation Hive.
- **Inviter** : lien d’invitation généré par l’application ; page **Rejoindre** (`/hive/rejoindre`).
- **Membres** (`/hive/ruches/<id>/membres`) : validation des demandes (créateur de la ruche), quotas, exclusion.

Le mode **tirage seul** (`pull_only`) : l’instance peut recevoir des fichiers sans en envoyer (affiché dans l’interface si applicable).

### Catalogue et partage

- **Catalogue Hive** : œuvres partagées par les pairs ; bouton **Suivre** côté récepteur.
- **Repartage automatique** : option **Repartager automatiquement les fichiers reçus** (`hive_catalog_auto_share`, **désactivée** par défaut).
- **Accueil** : section **Dernières dispos Hive** lorsque des entrées existent.
- Sur une **fiche média**, un seul bloc **Hive** regroupe partage, exclusions par ruche et état de préparation.

Avant qu’un fichier soit visible des pairs, l’instance calcule une **empreinte** (empreinte SHA par blocs du fichier) via un processus dédié dans le conteneur.

### Demandes et liste Recherché

Navigation **Hive** : onglets **Catalogue**, **Mes demandes**, **Reçues**, **Recherché**.

- Rien n’est partagé tant que vous ne l’avez pas décidé ; une **demande** prévient les membres qui possèdent déjà le fichier (demandes **anonymes** côté ruche).
- Si aucun pair ne l’a, le titre peut figurer dans **Recherché** pour qu’un autre membre se le procure puis le partage.
- **Réglages → Hive → Demandes à la ruche** : comportement quand la recherche locale ne trouve rien (`hive_request_on_miss` : automatique, me demander, jamais — avec nuances selon la présence d’une clé AllDebrid).

### Téléchargements

Hive est une **source** comme les autres dans l’ordre de téléchargement (`hive` dans `download_source_order`). Lorsqu’un fichier est disponible chez un pair :

1. MediaTracker obtient un **grant** de lecture temporaire, signé par le pair.
2. **aria2** (téléchargeur dans le conteneur) récupère le fichier en HTTP avec l’en-tête Hive prévu.
3. Le fichier apparaît dans **Téléchargements** puis est rangé comme un téléchargement AllDebrid classique.

Il n’y a **pas** d’écran de transferts Hive séparé : tout passe par la file **Téléchargements** habituelle.

Si le pair est injoignable ou **occupé** (limite de transferts, fenêtre horaire, débit saturé), MediaTracker peut **classer** les sources disponibles d’après les instantanés de charge reçus des pairs, choisir un pair moins chargé, puis **basculer** vers un autre pair ou une autre source de l’ordre **Réglages → Sources**.

Les pairs publient périodiquement un instantané : transferts sortants en cours, plafond de parallélisme, plafond de débit d’envoi, débit disponible estimé, indicateur **occupé**. Les données reçues lors des échanges Hive sont conservées un temps limité ; les entrées trop anciennes sont traitées comme inconnues.

### Limites de débit et fenêtre horaire

Toujours dans **Réglages → Hive** :

| Réglage | Plage | Rôle |
| --- | --- | --- |
| `hive_transfer_parallel` | 1–16 | Téléchargements simultanés depuis les pairs (défaut 2) |
| `hive_transfer_send_bps` | 0–2e9 | Débit d’envoi vers les pairs (0 = illimité) |
| `hive_transfer_recv_bps` | 0–2e9 | Débit de réception |
| `hive_transfer_window_start` / `end` | minutes dans la journée | Fenêtre horaire autorisée |

### Identité illisible

Si la clé privée Hive ne peut plus être lue, l’interface bloque la plupart des réglages Hive jusqu’à **Réinitialiser l’identité** (bouton dans Réglages ou `POST /api/v1/hive/identity/reset`).

### Plusieurs instances sur un même réseau

Chaque instance doit avoir :

- une **`SECRET_KEY` distincte** ;
- des volumes **`/config`** et **`/data`** séparés ;
- une **adresse publique** distincte (souvent derrière un proxy inverse).

Exemple de fichier Compose : [exemples/docker-compose-deux-instances-hive.yml](exemples/docker-compose-deux-instances-hive.yml).

Pour des scénarios avancés (proxy multi-domaines, sandboxes), voir aussi [exemples/docker-compose-hive-sandbox.example.yml](exemples/docker-compose-hive-sandbox.example.yml).

### Suivi dans Santé

**Santé → Performance** : encart **Ruche** avec vos envois en cours, débit sortant actuel, décompte de pairs joignables / occupés / inconnus, puis le détail par ruche (barre de charge, état libre / occupé / inconnu).

## Home Assistant

Chemin source : `docs/home-assistant.md`

<a id="docs-home-assistant"></a>

MediaTracker peut être piloté depuis Home Assistant via un **jeton d’API** et le package YAML fourni dans le dépôt.

### Documentation détaillée

Tout le flux d’installation (package, secrets, tableaux de bord, HACS, thèmes optionnels) est dans :

**[homeassistant/README.md](../homeassistant/README.md)**

### Résumé

1. **Créer un jeton** dans MediaTracker : **Réglages → Jetons d’API → Accès API**, portée **write** (inclut la lecture).
2. Copier `homeassistant/mediatracker.yaml` vers `/config/packages/` sur Home Assistant.
3. Définir dans `secrets.yaml` :
   - `mediatracker_url` : schéma + hôte + port, **sans chemin** (ex. `http://192.0.2.10:8080`)
   - `mediatracker_token` : jeton `mt_…`
4. Activer les packages dans `configuration.yaml` : `packages: !include_dir_named packages`.
5. Importer le tableau de bord `homeassistant/dashboard.yaml` (ou un thème sous `homeassistant/dashboards/`).

### API utilisée par le package

- `GET /api/v1/ha/summary` (capteur REST, environ une fois par minute)
- `POST` divers pour suivre, vérifier les sources, file d’attente, etc. (voir le package)

### Notifications depuis MediaTracker

Pour envoyer des événements **vers** Home Assistant sans le package HA, ajoutez une cible **Home Assistant** dans **Réglages → Notifications** (URL du serveur + identifiant de webhook).

L’assistant de premier démarrage peut pré-configurer une cible HA si vous renseignez l’étape Home Assistant.

## Mises à jour

Chemin source : `docs/mises-a-jour.md`

<a id="docs-mises-a-jour"></a>

### Manuelle (recommandée sur NAS prudents)

```bash
docker compose pull
docker compose up -d
```

Les données dans `/config` et `/data` sont conservées ; les migrations SQLite s’exécutent au démarrage.

### Watchtower

MediaTracker **ne monte pas** `docker.sock`. Seul un conteneur **Watchtower** (ou équivalent) accède à Docker.

#### Configuration Watchtower

Variables courantes (conteneur Watchtower) :

- `WATCHTOWER_HTTP_API_UPDATE=true` — API HTTP pour déclencher une mise à jour.
- `WATCHTOWER_HTTP_API_TOKEN` — secret partagé avec MediaTracker.
- `WATCHTOWER_HTTP_API_PERIODIC_POLLS` / `WATCHTOWER_POLL_INTERVAL` — vérification périodique des images.
- `WATCHTOWER_CLEANUP=true` — supprimer les anciennes images.
- `WATCHTOWER_CONTAINER_NAMES=mediatracker` — ne surveiller que ce conteneur (recommandé en stack séparé).

Exemples : [exemples/](exemples/README.md).

#### Côté MediaTracker

**Réglages → Jetons d’API** :

- **URL Watchtower** — ex. `http://watchtower:8080` sur le réseau Docker partagé, ou l’URL joignable depuis le conteneur MediaTracker.
- **Jeton Watchtower** — identique à `WATCHTOWER_HTTP_API_TOKEN`.

**Réglages → Mise à jour et redémarrage** :

- Affiche la version / SHA de l’image courante et compare au registre GHCR (jeton **GHCR** optionnel) ou à GitHub (**jeton GitHub** en repli).
- **Mettre à jour maintenant** : `POST` vers `{watchtower_url}/v1/update?image=…` avec le jeton Bearer.
- **Redémarrer** : redémarrage du processus applicatif sans forcément retélécharger l’image.
- Option **Me notifier quand Watchtower installe une nouvelle version** (`notify_on_update`).

En cas d’erreur 401 : le jeton Watchtower ne correspond pas à celui du conteneur.

### Image suivie

L’image publique documentée est `ghcr.io/nico2511/mediatracker-hive:latest`, fixée au build via la variable d’environnement `MEDIATRACKER_UPDATE_IMAGE` (défaut dans l’image publique : `ghcr.io/nico2511/mediatracker-hive`). Le bouton **Mettre à jour maintenant** transmet à Watchtower cette image de référence ; la comparaison GHCR peut s’effectuer sans jeton pour une image publique.

## Sauvegarde et restauration

Chemin source : `docs/sauvegarde.md`

<a id="docs-sauvegarde"></a>

### Sauvegardes automatiques

- Copie quotidienne de la base SQLite dans **`/config/backups/`**.
- Rétention : **`backup_keep`** jours (défaut 7, plage 1–90) — **Réglages → Disque et sauvegardes**.
- Le cache d’affiches TMDB sous `/config/cache/images` **n’est pas** inclus dans ces sauvegardes SQLite.

### `SECRET_KEY`

Gardez la même **`SECRET_KEY`** si vous restaurez une sauvegarde ou copiez `/config` : sinon les secrets chiffrés en base (clés API, jetons) deviennent illisibles.

### Depuis l’interface

**Réglages → Disque et sauvegardes** : composant **Sauvegarde / restauration** de la base.

- **Liste** des copies automatiques et des copies de sécurité (avant réinitialisation ou restauration).
- **Télécharger** : enregistre le fichier `.db` sur votre appareil (sauvegardes quotidiennes ou copies de sécurité).
- **Importer** : envoi d’un fichier `.db` ; l’application l’enregistre dans `backups/` puis ouvre le même flux qu’une restauration (aperçu : version, schéma, nombre de fiches, compatibilité).
- **Restaurer** : confirmation explicite ; une copie de sécurité de la base courante est créée avant remplacement. Après succès, rechargement de l’application.

Les sauvegardes dont le nom commence par `import-` proviennent d’un import manuel. Gardez la **`SECRET_KEY`** de l’instance d’origine si la base contient des secrets chiffrés ; l’identité **Hive** est conservée telle quelle dans le fichier importé.

### Réinitialiser la base

**Réglages → Réinitialiser la base** : efface fiches, téléchargements et index bibliothèque après une copie de secours dans `backups/`. **Les fichiers sur `/data` ne sont pas supprimés.**

### Sauvegarde complète NAS

Pour une reprise sur autre machine :

1. Arrêter le conteneur.
2. Copier le volume **`/config`** (base, backups, aria2.secret).
3. Copier le volume **`/data`** (médias, téléchargements, corbeille sur le **même** montage).

Vérifiez `PUID`/`PGID` et chemins `MOVIES_DIR` etc. sur le nouvel hôte.

## Dépannage et FAQ

Chemin source : `docs/depannage-faq.md`

<a id="docs-depannage-faq"></a>

### Conteneur « unhealthy »

Vérifiez :

- Droits sur **`/config`** (`PUID` / `PGID`).
- `SECRET_KEY` défini.
- Journaux : `docker logs mediatracker`.

Endpoint public : `GET /healthz`.

### Déplacement de fichier après téléchargement échoue

**Cause fréquente** : téléchargements et bibliothèque sur des volumes Docker **différents** (pas de renommage atomique).

**Correctif** : un seul montage `/data` pour films, séries, téléchargements et corbeille — voir [Installation](#docs-installation).

La page **Santé** et l’assistant signalent un avertissement volume si les chemins sont incohérents.

### Téléchargements en pause

- Espace disque sous le seuil **`disk_min_free_gb`** (défaut 5 Go, `0` désactive).
- AllDebrid invalide ou quota.
- aria2 injoignable (alerte après `aria2_unreachable_alert_min` minutes).

### Sources en panne

**Réglages → Sources** : pastilles d’état, dernier test, message (domaine Wawacity, Cloudflare, etc.).

- Wawacity : mettre à jour l’**URL de base** ; proposition de migration de domaine si l’instance le détecte.
- Aucune source active : message bloquant les téléchargements.
- **Dernier test** sur chaque carte : horodatage et issue du dernier essai (aucun téléchargement lancé). **Tester toutes** enchaîne une requête témoin par source activée.

### Profil de qualité modifié : plus de liens utilisables

Les liens **déjà enregistrés** restent en base, mais le téléchargement les **refuse** s’ils ne passent plus le profil actuel de la fiche (journal : `refusé (filtre profil)` ou message équivalent côté Comet/Torrentio).

Pour **découvrir de nouvelles pages** côté source (retéléchargement sans lien vivant, REDISCOVER, repli de version, réparation avec recherche réseau), le délai **`remote_search_cooldown_hours`** (défaut 6 h, réglable 1–72 h dans **Réglages → Sources**) s’applique **par fiche**, y compris sur une action lancée à la main. Tant que le délai n’est pas écoulé, le message du type « Aucun lien disponible, nouvel essai plus tard » ou « Recherche source reportée (limite X h) » peut apparaître. Baisser **temporairement** ce réglage (par ex. 1 h) permet de relancer une recherche distante plus tôt, puis de remettre la valeur habituelle.

### Connexion / session

- Derrière HTTPS : `PUBLIC_URL` et `FORWARDED_ALLOW_IPS` / `COOKIE_SECURE`.
- Verrouillage après échecs : `LOGIN_RATE_LIMIT`, fenêtre et durée `LOGIN_LOCKOUT_SECONDS`.

### Hive

- Pair injoignable ou occupé : repli vers un autre pair ou la source suivante ; vérifier **`hive_public_address`**, la fenêtre horaire et les limites de débit dans **Réglages → Hive**.
- **Santé → Performance → Ruche** : si tous les pairs sont « inconnus », peu d’échanges récents — attendre un échange catalogue ou un transfert pour rafraîchir les instantanés (données périmées après environ dix minutes sans nouvelle).
- **Identité perdue** : réinitialisation d’identité dans Réglages Hive.

### Mise à jour Watchtower

- « Watchtower injoignable » : URL réseau depuis le conteneur MediaTracker.
- « Jeton refusé » : aligner **Jetons d’API → Jeton Watchtower** et `WATCHTOWER_HTTP_API_TOKEN`.

### Où sont les journaux applicatifs ?

Rotatifs sous **`/config/logs/`** (`LOG_MAX_BYTES`, `LOG_BACKUP_COUNT` en env).

### API et intégrations

- OpenAPI : `/api/docs` (session requise).
- Home Assistant : [home-assistant.md](#docs-home-assistant).

### TMDB / identifiants

Pour corriger l’identifiant TMDB d’une **série** déjà suivie (aperçu, reconstruction ou conservation des épisodes), voir [TMDB — Corriger l’identifiant](#docs-tmdb-corriger-lidentifiant-tmdb-dune-serie).
