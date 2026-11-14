# Installation

Ce guide décrit l’installation de MediaTracker avec **Docker** et **Docker Compose** sur un NAS ou un serveur Linux.

## Prérequis

- **Docker** et **Docker Compose** installés sur la machine hôte.
- Une image récupérable depuis le registre : `ghcr.io/nico2511/mediatracker-hive:latest`.
- Un disque (ou partage) pour le volume de **configuration** et un montage **unique** pour les **données** média (voir ci-dessous).

## Fichiers Compose fournis

À la racine du dépôt :

| Fichier | Usage |
| --- | --- |
| [`docker-compose.yml`](../docker-compose.yml) | MediaTracker seul |
| [`docker-compose.jellyfin.yml`](../docker-compose.jellyfin.yml) | MediaTracker + Jellyfin (linuxserver) sur le même réseau Compose |
| [Exemples commentés](exemples/README.md) | Variantes NAS, réseau externe, Watchtower, Hive multi-instances |

Exemples prêts à copier : dossier [`docs/exemples/`](exemples/).

## Volumes

```yaml
volumes:
  - /chemin/vers/mtr/config:/config
  - /chemin/vers/medias:/data
```

- **`/config`** : base SQLite (`app.db`), journaux rotatifs, sauvegardes quotidiennes, fichier `aria2.secret`, cache d’affiches TMDB.
- **`/data`** : bibliothèque, dossier de téléchargements et corbeille — **tout sur le même montage**.

`PUID` / `PGID` : identifiants utilisateur et groupe pour les fichiers créés dans le conteneur (`id -u` / `id -g` sur l’hôte).

## Variables d’environnement obligatoires et utiles

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

## Démarrage

```bash
docker compose up -d
# ou
docker compose -f docker-compose.jellyfin.yml up -d
```

Ouvrez `http://<hôte>:8080`. Si aucun compte n’existe, l’assistant redirige vers `/configuration`.

## Points de contrôle

- Santé publique du conteneur : `GET /healthz`
- Santé applicative (session requise) : page **Santé** ou `GET /api/v1/health`
- Documentation OpenAPI : `/api/docs` (session requise)

Suite : [Premier démarrage](premier-demarrage.md).
