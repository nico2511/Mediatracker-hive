# Cartes HACS requises

Le tableau de bord `dashboard.yaml` utilise cinq cartes personnalisées. Sans elles, l'éditeur signale des cartes inconnues, et sans layout-card la vue ne se place pas. Le package `mediatracker.yaml` (capteurs, scripts, file d'attente) n'en dépend pas.

## À installer dans HACS

Frontend, puis télécharger chacune :

- [button-card](https://github.com/custom-cards/button-card) — petits boutons Pause, Reprendre, Sources, Nouveautés, Bibliothèque, Suivre, et les pastilles de la file.
- [Mushroom](https://github.com/piitaya/lovelace-mushroom) — tuiles Santé, Disque, Version, téléchargements et À voir.
- [card-mod](https://github.com/thomasloven/lovelace-card-mod) — fond des blocs, sans bordure superflue.
- [auto-entities](https://github.com/thomasloven/lovelace-auto-entities) — affiches réellement présentes, et liste « File d'attente (NAS éteint) ». Un capteur vide n'occupe pas de case.
- [layout-card](https://github.com/thomasloven/lovelace-layout-card) — vue `custom:grid-layout`. Trois colonnes sur un grand écran, une colonne sous 840 px. Les affiches restent en cases de 76 px.

Redémarrez Home Assistant après l'installation. HACS ajoute les ressources tout seul.

## Affiches sans image

Une `picture-entity` sans image dessine une carte rouge. Le tableau de bord ne l'utilise pas. Le bouton n'affiche `entity_picture` que si l'adresse commence par `http://` ou `https://`. Sinon l'icône grise reste à la place. Un capteur sans titre (`—`, inconnu, indisponible) est omis : pas de grande plage vide. Un appui ouvre TMDB. Un appui long met l'identifiant dans la file.

L'adresse de MediaTracker reste `mediatracker_url` dans `secrets.yaml`, lue via `sensor.mediatracker_adresse`. Le jeton ne va pas dans le tableau de bord. Voir `README.md` pour les fichiers à copier.
