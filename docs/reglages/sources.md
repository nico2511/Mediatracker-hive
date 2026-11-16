# Sources

URL : `/reglages/sources`

Les mêmes URLs peuvent être préremplies à l’**étape Sources** de l’assistant (`/configuration`) ; voir [Premier démarrage — Sources](../premier-demarrage.md).

## Présentation de la page

- Chaque source est une **carte** avec un interrupteur **Activée** / **Désactivée**, un libellé lisible (plus d’identifiant technique en titre) et un fil d’Ariane court (rôle de la source).
- Dans **Ordre des sources**, seules les sources **activées** se réordonnent ; les sources désactivées restent visibles en bas, grisées, avec la mention **désactivée**.
- Les URL longues (instances Comet, addons Stremio) sont **tronquées** avec boutons **Voir** et **Copier**.

## Ordre et activation

- **`download_source_order`** : liste ordonnée parmi `wawacity`, `free_telecharger`, `hive`, `comet`, `torrentio`, `stremio_addons`. Au moins une source doit rester cochée.
- État en ligne / panne : API `GET /api/v1/settings/sources/status` (affiché dans la page).

## Addons Stremio

Source `stremio_addons` : liste ordonnée d’addons `{ url, enabled }`, même ergonomie que les instances Comet (ajout, suppression, ordre).

- Une requête `…/stream/movie|series/…json` par addon activé ; cache et délai par domaine.
- Les segments d’URL contenant des clés de débrid ou de tracker sont **retirés** à la saisie ; une URL encore garnie d’un secret est refusée à l’appel.
- Les flux manifestement informatifs ou lecteurs seuls sont ignorés ; la sélection suit les **profils de qualité** (torrents, aimants, liens débridables).

## Rythme et cache

| Clé | Défaut (env) | Plage | Rôle |
| --- | --- | --- | --- |
| `source_request_delay_seconds` | 5 | 2–60 | Pause entre requêtes HTTP vers les sources |
| `source_cache_seconds` | 900 | 0–3600 | Cache des résultats de recherche |
| `source_error_backoff_min` | 180 | 30–1440 | Repousse après erreur source |
| `remote_search_cooldown_hours` | 6 | 1–72 | Délai entre deux recherches distantes d'une même fiche |

`remote_search_cooldown_hours` est réglable dans **Réglages → Sources**, section **Rythme des requêtes** (1 à 72 h, défaut 6). Il s’applique **par fiche** via `last_remote_search_at` : une nouvelle recherche distante n’est acceptée qu’après ce délai depuis la dernière qui a consommé le quota. Concernent notamment le **retéléchargement** sans lien vivant, les passages **REDISCOVER**, le **repli de version** et la **relecture source** d’une réparation lorsqu’une recherche réseau est nécessaire — **y compris si l’action est déclenchée à la main** (pas de dérogation). Un sondage qui se contente des pages déjà liées en base n’écrit pas ce quota.

## Hébergeurs (liens débrid)

- **`hoster_preference`** : ordre des hébergeurs acceptés pour les liens HTML, via une liste réordonnable dans l’interface.
- Les noms proposés combinent une liste **statique** connue de l’application et la liste **publique AllDebrid** (`/v4/hosts`, mise en cache côté serveur).
- Certains hébergeurs restent **refusés** par le serveur sans réglage utilisateur. Les erreurs de saisie affichent le **détail** renvoyé par l’API (par ex. hébergeur inconnu).

## URLs par source

| Clé | Source |
| --- | --- |
| `wawacity_base_url` | Wawacity (obligatoire pour scanner) |
| `wawacity_user_agent` | User-Agent optionnel |
| `free_telecharger_user_agent` | Free-Télécharger (URL via fiche fournisseur en base) |
| `torrentio_base_url` | Torrentio (défaut public dans l’image) |
| `comet_base_urls` | Liste d’instances Comet (une par ligne) ; `comet_base_url` garde la première |
| `stremio_addons` | JSON ou liste d’addons Stremio |

Migration de domaine Wawacity : bannière et actions **Accepter / Rétablir** si une redirection est proposée (`wawacity_domain_propose_only` en env au premier boot).

## Test de source

Bloc **Tester les sources** :

- **Titre** obligatoire (film, ou épisode avec suffixe `S01E02`). Année optionnelle entre parenthèses dans le titre.
- **Identifiant TMDB** optionnel : le serveur récupère titre, année et identifiant IMDb associé pour la sonde (sans modifier la base).
- **Tester** (sur une carte) ou **Tester toutes** : une requête témoin par source (ou **une ligne par addon Stremio activé**) ; aucun téléchargement.
- Les résultats reprennent les **messages exacts** du moteur (trouvé, aucun résultat, refus profil, erreur, etc.), la **durée** et le **profil de qualité** utilisé.
- Chaque carte affiche aussi le **dernier test** enregistré (horodatage, issue, motif, durée). Un test manuel « aucun résultat » remet la source **en ligne** et efface un ancien état de panne lié à ce test.

## Hive

Si Hive est activé et que vous appartenez à des ruches, la ligne d’état Hive affiche le nombre de ruches.

Voir [Chaîne de téléchargement](../telechargements.md).