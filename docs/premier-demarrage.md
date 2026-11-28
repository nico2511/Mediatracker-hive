# Premier démarrage

## Assistant de configuration

Route : `/configuration` (accessible sans session tant qu’aucun compte n’existe, ou pour terminer l’assistant).

Étapes affichées dans l’interface :

1. **Mot de passe** — création du compte `admin` (ou `ADMIN_USERNAME`) ; `setup_token` si le serveur l’exige.
2. **Dossiers** — `media_dir`, films, séries, téléchargements, corbeille ; avertissement si les chemins ne sont pas sur le même volume.
3. **AllDebrid** — clé API (optionnelle à ce stade, stockée chiffrée).
4. **TMDB** — jeton de lecture API v4.
5. **Jellyfin** — URL, clé API, utilisateur, correspondance de chemins.
6. **Sources** — URLs optionnelles des sources de liens (voir ci-dessous). Bouton **Passer** : rien n’est modifié.
7. **Home Assistant** — webhook optionnel (URL + identifiant de webhook).
8. **Terminer** — récapitulatif du scan initial.

### Étape Sources

Champs **tous optionnels** ; une valeur vide laisse le réglage actuel ou le défaut du conteneur :

| Champ assistant | Stockage (même clé que Réglages → Sources) |
| --- | --- |
| Wawacity | `wawacity_base_url` |
| Free-Télécharger | URL dans la fiche fournisseur `free_telecharger` (pas de clé SQLite dédiée) ; renseigner l’URL dans l’assistant ne l’active pas — l’activation se fait dans **Réglages → Sources** |
| Comet (une ligne par instance) | `comet_base_urls` |
| Torrentio | `torrentio_base_url` |
| Addons Stremio (optionnel) | `stremio_addons` |

Validation : chaque adresse renseignée doit commencer par `http://` ou `https://`. **Tester Wawacity** appelle le même test que dans l’assistant historique (`POST /api/v1/setup/probe`). Enregistrement groupé : `POST /api/v1/setup/sources`.

Hive n’a pas d’URL de base (activation et adresse publique dans **Réglages → Hive**).

Si un mot de passe administrateur existe déjà (variable `ADMIN_PASSWORD` au démarrage), l’assistant commence à l’étape **Dossiers** après connexion.

### Jeton d’installation (`SETUP_TOKEN`)

Sans compte et sans `SETUP_TOKEN` en variable d’environnement, MediaTracker génère un jeton au démarrage et l’écrit dans les **journaux** (niveau WARNING), du type :

```text
Aucun compte administrateur : pour créer le premier compte via l'assistant,
saisissez le jeton d'installation suivant (ou définissez SETUP_TOKEN pour le fixer) : …
```

Définir `SETUP_TOKEN` dans l’environnement fixe ce jeton et évite de le chercher dans les logs.

## Première connexion

- URL : `http://<hôte>:8080/login`
- Après l’assistant : accueil, **Suivre** (TMDB), **Téléchargements**, **Bibliothèque**, **Réglages**.

## Réglages complémentaires

Tout ce qui n’est pas dans l’assistant se configure dans **Réglages** — voir [l’index des sections](reglages/README.md) :

- Ordre et activation des **sources** (`/reglages/sources`)
- **Profils de qualité** et repli global
- **Hive** (désactivé par défaut)
- **Watchtower** et jetons GHCR (`/reglages/jetons`, `/reglages/maj`)

## PWA

**Réglages → Application** propose d’installer MediaTracker comme application (navigateur compatible).
