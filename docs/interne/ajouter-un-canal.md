# Ajouter un canal de notification

Les événements passent par `events/deliver.py` : payload déjà **nettoyé** (`scrub_payload`), puis `for_hook(webhook)` choisit le canal selon `webhook.type`.

Références : `events/channels/registry.py`, contrat `NotificationChannel` dans `events/channels/base.py`, implémentations `ntfy_channel.py`, `homeassistant_channel.py`, `generic_channel.py`, enregistrement dans `events/channels/__init__.py`.

## Contrat `NotificationChannel`

| Membre | Rôle |
|--------|------|
| `name` | Valeur de `Webhook.type` gérée par ce canal (`ntfy`, `homeassistant`, `generic`). |
| `is_enabled(hook)` | Livrer ou non (souvent `hook.enabled`). |
| `render(hook, event_type, payload, secret_key)` | Corps HTTP (`bytes`) et en-têtes ; le payload est déjà scrubé. |

`for_hook` (`registry.py`) : type inconnu → log DEBUG et **repli sur `homeassistant`** (`_FALLBACK_TYPE`).

## Étapes

1. **Fichier** `backend/mediatracker/events/channels/mon_canal_channel.py` — classe avec `name`, `is_enabled`, `render`.
2. **Ligne** dans `events/channels/__init__.py` : `register(MonCanalChannel())` (instancier une fois, comme les canaux existants).

Le type doit correspondre à ce que l'API Réglages enregistre sur la ligne `webhook`. Pas de log du corps POST ni des secrets déchiffrés.

## Capacités des canaux intégrés

| Canal | Format | Secret |
|-------|--------|--------|
| `homeassistant` | JSON (`format_homeassistant`) | Aucun dans les en-têtes ; l'URL webhook porte l'id. |
| `ntfy` | Texte (`format_ntfy`) | Jeton topic via `decrypt_secret(hook.secret, secret_key)` pour l'en-tête d'auth ntfy. |
| `generic` | JSON + signature HMAC (`signature_headers`) | Secret webhook déchiffré pour signer le corps. |

Tous passent par `scrub_payload` avant formatage.

## Exemple minimal (fictif)

```python
# backend/mediatracker/events/channels/exemple_channel.py
import json
from typing import Any
from mediatracker.db.models import Webhook
from mediatracker.events.scrub import scrub_payload

class ExempleChannel:
    name = "exemple"

    def is_enabled(self, hook: Webhook) -> bool:
        return bool(hook.enabled)

    def render(
        self,
        hook: Webhook,
        event_type: str,
        payload: dict[str, Any],
        secret_key: str,
    ) -> tuple[bytes, dict[str, str]]:
        del hook, secret_key
        safe = scrub_payload(payload)
        body = json.dumps({"event": event_type, "titre": safe.get("title")}).encode()
        return body, {"Content-Type": "application/json"}
```

```python
# events/channels/__init__.py
from mediatracker.events.channels.exemple_channel import ExempleChannel
register(ExempleChannel())
```

## Tests à écrire

S'inspirer de `tests/test_notification_channels.py` :

- `for_hook` avec `type` inconnu → canal `homeassistant` et message DEBUG.
- `all_channels` / `known_types` incluent le nouveau nom.
- Enregistrer un faux canal dans `_by_name` (avec restauration en `finally`), vérifier `is_enabled` et `render` (corps et en-têtes sans secret en clair).

Pour la livraison bout en bout : mocker `post_request` dans les tests de `deliver` si besoin.

## Pièges

- **Repli** : un ancien `webhook.type` non enregistré part en Home Assistant ; documenter la migration si vous renommez un type.
- **Secrets** : lire `hook.secret` uniquement via `decrypt_secret` ; ne jamais les écrire dans les logs ni renvoyer le jeton dans un en-tête de debug.
- **Payload** : toujours repasser par `scrub_payload` dans `render`, même si l'appelant a déjà scrubé — défense en profondeur.
- **SSRF** : l'URL du webhook est validée à l'envoi (`enforce_outbound_request_url`) ; pas de contournement côté canal.

Les canaux n'appellent pas les sites sources ; la cadence HTTP des sources (`source_request_delay_seconds`) ne s'applique pas ici.
