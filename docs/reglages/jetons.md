# Jetons d'API

URL : `/reglages/jetons`

## Secrets sortants (services distants)

| Clé | Usage |
| --- | --- |
| `ghcr_read_token` | Lecture métadonnées image GHCR (comparaison de version) |
| `github_update_token` | Repli : dernier commit sur `main` si GHCR absent |
| `watchtower_url` | Base URL API Watchtower (`http://watchtower:8080`) |
| `watchtower_token` | Bearer identique à `WATCHTOWER_HTTP_API_TOKEN` |

Secrets jamais réaffichés après enregistrement (chiffrement en base).

## Accès API (entrants)

Jetons `mt_…` créés dans l’UI : portées `read`, `write`, `admin`. Secret visible **une seule fois** à la création. Usage typique : Home Assistant, scripts LAN.
