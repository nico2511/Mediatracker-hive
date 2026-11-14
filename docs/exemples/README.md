# Exemples de configuration

Fichiers **commentés** à copier hors du dépôt ou à adapter sur le NAS. Remplacez `changeme`, les chemins `/chemin/...` et les hôtes d’exemple.

| Fichier | Scénario |
| --- | --- |
| [docker-compose-nas-simple.yml](docker-compose-nas-simple.yml) | Instance seule, montage unique `/data` + `/config` |
| [docker-compose-jellyfin-reseau-externe.yml](docker-compose-jellyfin-reseau-externe.yml) | Jellyfin déjà sur un réseau Docker `medias` externe |
| [docker-compose-watchtower-separe.yml](docker-compose-watchtower-separe.yml) | Watchtower dans un stack séparé (API HTTP, filtre par nom de conteneur) |
| [docker-compose-deux-instances-hive.yml](docker-compose-deux-instances-hive.yml) | Deux MediaTracker pour recette Hive (volumes et clés distincts) |
| [docker-compose-hive-sandbox.example.yml](docker-compose-hive-sandbox.example.yml) | Proxy nginx multi-domaines pour recette Hive T8 (sans build d’image) |
| [env.example](env.example) | Variables d’environnement commentées (alignées sur `config.py`) |

Compose racine du dépôt : [`docker-compose.yml`](../../docker-compose.yml), [`docker-compose.jellyfin.yml`](../../docker-compose.jellyfin.yml).

Validation locale :

```bash
docker compose -f docs/exemples/docker-compose-nas-simple.yml config >/dev/null
```
