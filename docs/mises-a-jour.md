# Mises à jour

## Manuelle (recommandée sur NAS prudents)

```bash
docker compose pull
docker compose up -d
```

Les données dans `/config` et `/data` sont conservées ; les migrations SQLite s’exécutent au démarrage.

## Watchtower

MediaTracker **ne monte pas** `docker.sock`. Seul un conteneur **Watchtower** (ou équivalent) accède à Docker.

### Configuration Watchtower

Variables courantes (conteneur Watchtower) :

- `WATCHTOWER_HTTP_API_UPDATE=true` — API HTTP pour déclencher une mise à jour.
- `WATCHTOWER_HTTP_API_TOKEN` — secret partagé avec MediaTracker.
- `WATCHTOWER_HTTP_API_PERIODIC_POLLS` / `WATCHTOWER_POLL_INTERVAL` — vérification périodique des images.
- `WATCHTOWER_CLEANUP=true` — supprimer les anciennes images.
- `WATCHTOWER_CONTAINER_NAMES=mediatracker` — ne surveiller que ce conteneur (recommandé en stack séparé).

Exemples : [exemples/](exemples/README.md).

### Côté MediaTracker

**Réglages → Jetons d’API** :

- **URL Watchtower** — ex. `http://watchtower:8080` sur le réseau Docker partagé, ou l’URL joignable depuis le conteneur MediaTracker.
- **Jeton Watchtower** — identique à `WATCHTOWER_HTTP_API_TOKEN`.

**Réglages → Mise à jour et redémarrage** :

- Affiche la version / SHA de l’image courante et compare au registre GHCR (jeton **GHCR** optionnel) ou à GitHub (**jeton GitHub** en repli).
- **Mettre à jour maintenant** : `POST` vers `{watchtower_url}/v1/update?image=…` avec le jeton Bearer.
- **Redémarrer** : redémarrage du processus applicatif sans forcément retélécharger l’image.
- Option **Me notifier quand Watchtower installe une nouvelle version** (`notify_on_update`).

En cas d’erreur 401 : le jeton Watchtower ne correspond pas à celui du conteneur.

## Image suivie

L’image publique documentée est `ghcr.io/nico2511/mediatracker-hive:latest`, fixée au build via la variable d’environnement `MEDIATRACKER_UPDATE_IMAGE` (défaut dans l’image publique : `ghcr.io/nico2511/mediatracker-hive`). Le bouton **Mettre à jour maintenant** transmet à Watchtower cette image de référence ; la comparaison GHCR peut s’effectuer sans jeton pour une image publique.
