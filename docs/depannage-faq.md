# Dépannage et FAQ

## Conteneur « unhealthy »

Vérifiez :

- Droits sur **`/config`** (`PUID` / `PGID`).
- `SECRET_KEY` défini.
- Journaux : `docker logs mediatracker`.

Endpoint public : `GET /healthz`.

## Déplacement de fichier après téléchargement échoue

**Cause fréquente** : téléchargements et bibliothèque sur des volumes Docker **différents** (pas de renommage atomique).

**Correctif** : un seul montage `/data` pour films, séries, téléchargements et corbeille — voir [Installation](installation.md).

La page **Santé** et l’assistant signalent un avertissement volume si les chemins sont incohérents.

## Téléchargements en pause

- Espace disque sous le seuil **`disk_min_free_gb`** (défaut 5 Go, `0` désactive).
- AllDebrid invalide ou quota.
- aria2 injoignable (alerte après `aria2_unreachable_alert_min` minutes).

## Sources en panne

**Réglages → Sources** : pastilles d’état, dernier test, message (domaine Wawacity, Cloudflare, etc.).

- Wawacity : mettre à jour l’**URL de base** ; proposition de migration de domaine si l’instance le détecte.
- Aucune source active : message bloquant les téléchargements.
- **Dernier test** sur chaque carte : horodatage et issue du dernier essai (aucun téléchargement lancé). **Tester toutes** enchaîne une requête témoin par source activée.

## Profil de qualité modifié : plus de liens utilisables

Les liens **déjà enregistrés** restent en base, mais le téléchargement les **refuse** s’ils ne passent plus le profil actuel de la fiche (journal : `refusé (filtre profil)` ou message équivalent côté Comet/Torrentio).

Pour **découvrir de nouvelles pages** côté source (retéléchargement sans lien vivant, REDISCOVER, repli de version, réparation avec recherche réseau), le délai **`remote_search_cooldown_hours`** (défaut 6 h, réglable 1–72 h dans **Réglages → Sources**) s’applique **par fiche**, y compris sur une action lancée à la main. Tant que le délai n’est pas écoulé, le message du type « Aucun lien disponible, nouvel essai plus tard » ou « Recherche source reportée (limite X h) » peut apparaître. Baisser **temporairement** ce réglage (par ex. 1 h) permet de relancer une recherche distante plus tôt, puis de remettre la valeur habituelle.

## Connexion / session

- Derrière HTTPS : `PUBLIC_URL` et `FORWARDED_ALLOW_IPS` / `COOKIE_SECURE`.
- Verrouillage après échecs : `LOGIN_RATE_LIMIT`, fenêtre et durée `LOGIN_LOCKOUT_SECONDS`.

## Hive

- Pair injoignable : repli vers une autre source ; vérifier **`hive_public_address`** et le reverse proxy.
- **Identité perdue** : réinitialisation d’identité dans Réglages Hive.

## Mise à jour Watchtower

- « Watchtower injoignable » : URL réseau depuis le conteneur MediaTracker.
- « Jeton refusé » : aligner **Jetons d’API → Jeton Watchtower** et `WATCHTOWER_HTTP_API_TOKEN`.

## Où sont les journaux applicatifs ?

Rotatifs sous **`/config/logs/`** (`LOG_MAX_BYTES`, `LOG_BACKUP_COUNT` en env).

## API et intégrations

- OpenAPI : `/api/docs` (session requise).
- Home Assistant : [home-assistant.md](home-assistant.md).

## TMDB / identifiants

Pour corriger l’identifiant TMDB d’une **série** déjà suivie (aperçu, reconstruction ou conservation des épisodes), voir [TMDB — Corriger l’identifiant](tmdb.md#corriger-lidentifiant-tmdb-dune-série).
