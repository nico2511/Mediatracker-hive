# Sources

URL : `/reglages/sources`

Les mêmes URLs peuvent être préremplies à l’**étape Sources** de l’assistant (`/configuration`) ; voir [Premier démarrage — Sources](../premier-demarrage.md).

## Ordre et activation

- **`download_source_order`** : liste ordonnée parmi `wawacity`, `free_telecharger`, `hive`, `comet`, `torrentio`. Au moins une source doit rester cochée.
- État en ligne / panne : API `GET /api/v1/settings/sources/status` (affiché dans la page).

## Rythme et cache

| Clé | Défaut (env) | Plage | Rôle |
| --- | --- | --- | --- |
| `source_request_delay_seconds` | 5 | 2–60 | Pause entre requêtes HTTP vers les sources |
| `source_cache_seconds` | 900 | 0–3600 | Cache des résultats de recherche |
| `source_error_backoff_min` | 180 | 30–1440 | Repousse après erreur source |
| `remote_search_cooldown_hours` | 6 | 1–72 | Délai entre deux recherches distantes d'une même fiche |

`remote_search_cooldown_hours` est réglable dans **Réglages → Sources**, section **Rythme des requêtes** (1 à 72 h, défaut 6). Il s’applique **par fiche** via `last_remote_search_at` : une nouvelle recherche distante n’est acceptée qu’après ce délai depuis la dernière qui a consommé le quota. Concernent notamment le **retéléchargement** sans lien vivant, les passages **REDISCOVER**, le **repli de version** et la **relecture source** d’une réparation lorsqu’une recherche réseau est nécessaire — **y compris si l’action est déclenchée à la main** (pas de dérogation). Un sondage qui se contente des pages déjà liées en base n’écrit pas ce quota.

## Hébergeurs (liens débrid)

- **`hoster_preference`** : ordre CSV des hébergeurs acceptés pour les liens HTML. Certains hébergeurs sont **refusés** par le serveur sans réglage utilisateur.

## URLs par source

| Clé | Source |
| --- | --- |
| `wawacity_base_url` | Wawacity (obligatoire pour scanner) |
| `wawacity_user_agent` | User-Agent optionnel |
| `free_telecharger_user_agent` | Free-Télécharger (URL via fiche fournisseur en base) |
| `torrentio_base_url` | Torrentio (défaut `https://torrentio.strem.fun`) |
| `comet_base_urls` | Liste d’instances Comet (une par ligne) ; `comet_base_url` garde la première |

Migration de domaine Wawacity : bannière et actions **Accepter / Rétablir** si une redirection est proposée (`wawacity_domain_propose_only` en env au premier boot).

## Test de source

Bloc **Tester une source** : titre optionnel et identifiant IMDb optionnel.

- **Tester** (sur une carte source) ou **Tester toutes** : une requête témoin par source activée ; aucun téléchargement n’est lancé.
- Chaque carte affiche le **dernier test** : horodatage, issue (`trouvé`, `aucun résultat`, `erreur`, `refusée`, etc.), motif éventuel et durée en ms.
- Un test manuel qui aboutit à « aucun résultat » remet la source **en ligne** et efface un ancien état de panne lié à ce test.

## Hive

Si Hive est activé et que vous appartenez à des ruches, la ligne d’état Hive affiche le nombre de ruches.

Voir [Chaîne de téléchargement](../telechargements.md).
