# Sources

URL : `/reglages/sources`

Page dédiée (composant séparé de l’écran générique). Les mêmes URL peuvent être préremplies à l’étape **Sources** de l’assistant (`/configuration`) — voir [Premier démarrage](../premier-demarrage.md).

Sans **AllDebrid connecté** ni source **Hive** utilisable, un bandeau bloque la page et renvoie vers [Services et jetons](jetons.md) ou [Hive](hive.md).

## Présentation

- Chaque source est une **carte** : interrupteur Activée / Désactivée, libellé lisible, courte description du rôle.
- Dans **Ordre des sources**, seules les sources **disponibles** (selon AllDebrid / Hive) se réordonnent ; les autres peuvent être masquées avec un message *Disponible après connexion d’AllDebrid*.
- Les URL longues (instances Comet, addons Stremio) sont tronquées avec **Voir** et **Copier**.

## Ordre et activation

- Clé **`download_source_order`** : liste ordonnée parmi `wawacity`, `free_telecharger`, `hive`, `comet`, `torrentio`, `stremio_addons`. Au moins une source doit rester activée dans l’ordre enregistré.
- État en ligne / panne : affiché sur chaque carte ; API `GET /api/v1/settings/sources/status`.

## Sans clé AllDebrid

- Sources **qui passent par AllDebrid** (Wawacity, Free-Télécharger, Comet, Torrentio, Stremio) : cartes et réglages détaillés masqués ou indisponibles ; elles ne sont pas proposées dans l’ordre affiché.
- **Hive** : reste configurable si Hive est activé dans [Hive](hive.md).
- Panneau **Rythme des requêtes** et **ordre des hébergeurs** : visible seulement si AllDebrid est connecté.

## Rythme et cache

| Clé | Défaut (env) | Plage | Rôle |
| --- | --- | --- | --- |
| `source_request_delay_seconds` | 5 | 2–60 | Pause entre requêtes HTTP vers les sources |
| `source_cache_seconds` | 900 | 0–3600 | Cache des résultats de recherche |
| `source_error_backoff_min` | 180 | 30–1440 | Repousse après erreur source |
| `remote_search_cooldown_hours` | 6 | 1–72 | Délai entre deux recherches distantes d’une même fiche |

`remote_search_cooldown_hours` s’applique **par fiche** : retéléchargement, repli de profil, relecture source, y compris actions manuelles (pas de dérogation si une recherche réseau est nécessaire).

## Hébergeurs (liens HTML)

- Clé **`hoster_preference`** : ordre des hébergeurs acceptés, liste réordonnable dans **Rythme des requêtes**.
- Noms proposés : liste statique de l’application **plus** hôtes publics AllDebrid (mis en cache côté serveur).
- **`uptobox`** : **hébergeur interdit** — absent des listes, refusé à l’enregistrement et ignoré à la sélection de liens, même s’il apparaît encore sur une page source.
- **`1fichier`** est placé en dernier par défaut.

## URLs par source

| Clé | Source |
| --- | --- |
| `wawacity_base_url` | Wawacity (obligatoire pour scanner) |
| `wawacity_user_agent` | User-Agent optionnel |
| `free_telecharger_user_agent` | Free-Télécharger (URL via fiche fournisseur en base) |
| `torrentio_base_url` | Torrentio (défaut public dans l’image) |
| `comet_base_urls` | Liste d’instances Comet (une par ligne) ; `comet_base_url` garde la première |
| `stremio_addons` | JSON ou liste d’addons Stremio |

Migration de domaine Wawacity : bannière et actions **Accepter / Rétablir** si une redirection est proposée.

## Addons Stremio

Source `stremio_addons` : liste ordonnée `{ url, enabled }`, même ergonomie que Comet.

- Une requête stream par addon activé ; cache et délai partagés avec les autres sources.
- Segments d’URL contenant clés débrid ou tracker **retirés** à la saisie.
- Flux informatifs ou lecteurs seuls ignorés ; sélection selon [Profils de qualité](qualite.md).

## Test de source

Bloc **Tester les sources** :

- **Titre** obligatoire (film, ou épisode avec suffixe `S01E02`). Année optionnelle entre parenthèses.
- **Identifiant TMDB** optionnel pour enrichir la sonde sans modifier la base.
- **Tester** (carte) ou **Tester toutes** : une requête témoin par source (ou une par addon Stremio activé) ; **aucun téléchargement**.
- Résultats : messages du moteur, durée, profil utilisé ; dernier test mémorisé par carte.

## Hive

Si Hive est activé et que vous appartenez à des ruches, l’interface peut afficher le nombre de ruches.

Voir [Chaîne de téléchargement](../telechargements.md) et [Hive](../hive.md).