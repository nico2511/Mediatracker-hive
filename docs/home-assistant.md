# Home Assistant

MediaTracker peut être piloté depuis Home Assistant via un **jeton d’API** et le package YAML fourni dans le dépôt.

## Documentation détaillée

Tout le flux d’installation (package, secrets, tableaux de bord, HACS, thèmes optionnels) est dans :

**[homeassistant/README.md](../homeassistant/README.md)**

## Résumé

1. **Créer un jeton** dans MediaTracker : **Réglages → Jetons d’API → Accès API**, portée **write** (inclut la lecture).
2. Copier `homeassistant/mediatracker.yaml` vers `/config/packages/` sur Home Assistant.
3. Définir dans `secrets.yaml` :
   - `mediatracker_url` : schéma + hôte + port, **sans chemin** (ex. `http://192.0.2.10:8080`)
   - `mediatracker_token` : jeton `mt_…`
4. Activer les packages dans `configuration.yaml` : `packages: !include_dir_named packages`.
5. Importer le tableau de bord `homeassistant/dashboard.yaml` (ou un thème sous `homeassistant/dashboards/`).

## API utilisée par le package

- `GET /api/v1/ha/summary` (capteur REST, environ une fois par minute)
- `POST` divers pour suivre, vérifier les sources, file d’attente, etc. (voir le package)

## Notifications depuis MediaTracker

Pour envoyer des événements **vers** Home Assistant sans le package HA, ajoutez une cible **Home Assistant** dans **Réglages → Notifications** (URL du serveur + identifiant de webhook).

L’assistant de premier démarrage peut pré-configurer une cible HA si vous renseignez l’étape Home Assistant.
