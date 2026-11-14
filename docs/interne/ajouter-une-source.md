# Ajouter une source

MediaTracker sépare deux couches :

1. **Provider** (`backend/mediatracker/providers/sources/wawacity.py`, `comet.py`, `manual.py`) — logique métier, objets neutres (`SourceResult`, `LinkCandidate`, `EpisodeQuery`, `SourcePreferences` dans `providers/base.py`).
2. **Entrée de registre** (`*_plugin.py`) — branche le provider sur le poll, les liaisons de pages et le repli de téléchargement (`registry.py`, contrat `SourceEntry` dans `registry_base.py`).

Références : `wawacity_plugin.py`, `comet_plugin.py`, `manual_plugin.py`, enregistrement dans `providers/sources/__init__.py`.

## Contrat `SourceEntry`

| Membre | Rôle |
|--------|------|
| `name` | Identifiant stable (`wawacity`, `comet`, `manual`). |
| `capabilities` | Sous-ensemble de `links`, `latest`, `episode`. |
| `download_rank` | Optionnel. Plus petit = essayé en premier (`wawacity` 10, `comet` 20). Absent pour `manual`. |
| `provider_id(db)` | Id de la ligne `provider` ; erreur si absente. |
| `binding_page_url(...)` | URL absolue d'une liaison stockée. |
| `open_poll` / `open_cached` | Client live pour le suivi / les nouveautés ; `None` si non configuré. |
| `probe_binding_page` | Un GET cadencé sur une page liée ; filtres hébergeurs dans le plugin. |
| `open_episode` | Client pour le repli épisode ; `None` si non supporté. |

`for_type` (`registry.py`) ignore un type inconnu (log DEBUG, pas d'exception).

## Étapes

1. **Fichier** `backend/mediatracker/providers/sources/mon_site_plugin.py` — classe qui délègue au module provider existant ou nouveau.
2. **Ligne** dans `providers/sources/__init__.py` : `register(MonSiteSourceEntry())`.

Côté provider (si besoin) : `kind = "source"`, `type` court, `config_model()` avec `base_url` si le domaine change, pas de secret débrideur dans la config. Enregistrement provider : `register_builtins` (`providers/builtin.py`) ou entry point `mediatracker.providers`.

Au démarrage, une ligne `provider` est créée pour chaque type avec `links` ou `episode`. L'URL legacy (`{type}_base_url`) est recopiée dans `provider.config` si vide, sans effacer le réglage.

## Capacités

| Capacité | Comportement |
|----------|----------------|
| `links` | Poll des liens déjà stockés ; `open_poll` renvoie un client avec `get_links`. |
| `latest` | Pages nouveautés ; `open_cached` + `latest_releases`. |
| `episode` | Repli après épuisement des hébergeurs ; `open_episode` + `links_for` sur le client. |

La file route par capacité, pas par nom (`unlock._from_named_source`). L'ordre des téléchargements vient de `GET /api/v1/providers/types` et du réglage d'ordre.

## Exemple minimal (fictif)

```python
# backend/mediatracker/providers/sources/exemple_plugin.py
class ExempleSourceEntry:
    name = "exemple"
    capabilities = frozenset({"episode"})
    download_rank = 30

    def provider_id(self, db):
        ...

    def binding_page_url(self, db, settings, binding):
        return None

    def open_poll(self, db, settings, **kwargs):
        return None

    def open_cached(self, db, settings, **kwargs):
        return None

    def probe_binding_page(self, db, settings, page_url, *, transport=None):
        from mediatracker.core.source_binding import SourceBindingProbe
        return SourceBindingProbe(False, None, {}, message="Non supporté.")

    def open_episode(self, settings, *, base_url=None, transport=None):
        return None  # ou ExempleProvider.open_episode(...)
```

```python
# providers/sources/__init__.py
from mediatracker.providers.sources.exemple_plugin import ExempleSourceEntry
register(ExempleSourceEntry())
```

## Tests à écrire

S'inspirer de :

- `tests/test_source_registry.py` — enregistrement, `for_type` inconnu, faux plugin avec `open_poll` et session injectée.
- `tests/test_source_fallback_registry.py` — `known_types`, `download_rank`, routage `links` / `episode`, repli Wawacity → Comet sans réseau.
- `tests/test_source_modularity.py` — ordre `wawacity,comet`, refus d'un type absent, copie d'URL au boot.

Parser une réponse figée (fixture HTML/JSON) dans le module provider ; vérifier qu'aucun champ brut du site ne sort du module.

## Pièges

- **Cadence** : utiliser le pacer partagé (`loading._shared_pacer`, `source_request_delay_seconds`) et le cache HTML ; ne pas enchaîner les requêtes plus vite que le réglage. En cas d'erreur, respecter le backoff du poll.
- **Hébergeurs** : ne jamais proposer **uptobox** (`BLOCKED_SOURCE_HOSTERS`, filtre `_KEEP_HOSTERS` côté Wawacity). **1fichier** est autorisé en dernier dans l'ordre des hébergeurs.
- **Secrets** : clés API et jetons chiffrés en base (`decrypt_secret`) ; jamais dans les logs ni dans le corps des événements non nettoyés.
- **URL vide** : `open_*` renvoie `None` ; la source est ignorée pour ce passage.
- **Liens débridés** : ne pas stocker ni logger l'URL directe AllDebrid.
