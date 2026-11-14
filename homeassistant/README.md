# Tableau de bord Home Assistant

Home Assistant interroge MediaTracker avec un jeton d'API (`Authorization: Bearer`), une fois par minute, via `GET /api/v1/ha/summary`. Les boutons appellent des `POST` déjà prévus.

Le tableau de bord est compact : tuiles Mushroom, petits boutons, rangées d'affiches, formulaire Suivre, et la file d'attente. La vue est `custom:grid-layout` (layout-card) : trois colonnes sur un grand écran, une colonne sous 840 px. Il faut aussi button-card, Mushroom, card-mod et auto-entities. Le détail est dans `HACS.md`.

L'adresse de MediaTracker se définit **une seule fois**, dans `secrets.yaml`, clé `mediatracker_url` (schéma, hôte et port, sans chemin). Le package la publie avec `!secret mediatracker_url` dans `sensor.mediatracker_adresse`. Le capteur REST, chaque `rest_command`, les affiches relatives et le lien « Ouvrir MediaTracker » lisent ce capteur. Ne recopiez pas l'hôte dans `mediatracker.yaml` ni dans `dashboard.yaml`. N'y écrivez jamais le jeton.

Le capteur ne contacte pas AllDebrid, TMDB, Jellyfin ni Wawacity : il lit la base et l'espace disque local. « Vérifier les sources » demande au worker de passer les fiches suivies une par une, avec le délai déjà configuré entre les requêtes.

## Fichiers à copier

| Fichier du dépôt | Destination |
| --- | --- |
| `homeassistant/mediatracker.yaml` | `/config/packages/mediatracker.yaml` |
| `homeassistant/secrets.yaml` | les deux clés, dans `/config/secrets.yaml` (ne pas réutiliser le jeton d'exemple) |
| `homeassistant/dashboard.yaml` | éditeur de configuration brut du tableau de bord |

`homeassistant/secrets.yaml` est un exemple. Le seul hôte qui y figure ne doit pas être recopié ailleurs.

## Dashboards

Outre le tableau de bord compact `dashboard.yaml` (racine de ce dossier), le dépôt propose une présentation optionnelle, **Nuit flat**, sous `dashboards/nuit-flat/`. Elle réutilise le même package `mediatracker.yaml` et les mêmes secrets ; seuls le YAML Lovelace et une carte JavaScript changent.

| Thème | Dossier | Fichier principal | Particularités |
| --- | --- | --- | --- |
| **Référence (défaut)** | `homeassistant/` | `dashboard.yaml` | Mushroom + layout-card, grille 3 colonnes, décrit ci-dessous. |
| **Nuit flat** | `dashboards/nuit-flat/` | `dashboards/mediatracker-console-carte.yaml` | Carte `custom:mt-console-card` (commandes `/suivre`, `/file`, `/statut`). |

### Prérequis HACS

Le tableau par défaut demande **button-card**, **Mushroom**, **card-mod**, **auto-entities** et **layout-card** (détail dans `HACS.md`).

Installez les dépôts via HACS → Frontend, redémarrez Home Assistant, puis vérifiez que les ressources Lovelace sont actives (Paramètres → Tableaux de bord → Ressources).

### Installation par thème

1. Copiez d’abord le **package** et les **secrets** comme aux sections 2–3 ci-dessous (`mediatracker.yaml`, `mediatracker_url`, `mediatracker_token`).
2. **Nuit flat** :
   - Copiez `dashboards/nuit-flat/www/mediatracker/mt-console-card.js` vers `/config/www/mediatracker/mt-console-card.js` sur Home Assistant.
   - Paramètres → Tableaux de bord → Ressources → ajoutez l’URL **`/local/mediatracker/mt-console-card.js`**, type **module JavaScript**.
   - Créez le tableau de bord à partir de `dashboards/nuit-flat/dashboards/mediatracker-console-carte.yaml`.
   - Adaptez les entités `file_entity`, `statut_entity` et `message_entity` de la carte : l’exemple référence des noms qui ne font pas partie du package MediaTracker fourni ici (voir écarts ci-dessous).

Le thème optionnel utilise souvent **`binary_sensor.nas`** (NAS allumé/éteint) pour griser des boutons. Cette entité est **locale à votre installation** : créez-la vous-même (ping, intégration NAS, etc.) ou remplacez-la dans le YAML.

### Écarts d’entités par rapport au package `mediatracker.yaml`

Le package expose notamment `sensor.mediatracker`, `sensor.mediatracker_adresse`, `sensor.mediatracker_sante`, `sensor.mediatracker_file`, les capteurs dérivés (`sensor.mediatracker_telechargements`, …), `input_text.mediatracker_tmdb_id`, `input_select.mediatracker_type`, `input_text.mediatracker_file_attente`, les scripts `script.mediatracker_suivre`, `script.mediatracker_suivre_sortie`, `script.mediatracker_suivre_antenne`, `script.mediatracker_verifier_mise_a_jour`, et les `rest_command.mediatracker_*` listés dans le package.

| Référence dans un thème exporté | Présent dans `mediatracker.yaml` | Action suggérée |
| --- | --- | --- |
| `binary_sensor.nas` | Non (entité utilisateur) | Définir chez vous ou retirer la logique NAS du YAML. |
| `sensor.mediatracker_statut` (carte Nuit flat) | Non — équivalent : `sensor.mediatracker_sante` ou état de `sensor.mediatracker` | Pointer `statut_entity` vers `sensor.mediatracker_sante`. |
| `input_text.mt_dernier_message` (carte Nuit flat) | Non | Créer un helper `input_text` local, ou lier à une entité de notification existante. |
| Scripts `mt_file`, `mt_statut`, `mt_suivre` (carte JS) | Non — le package fournit `script.mediatracker_suivre` et la file via `input_text.mediatracker_file_attente` | Ajouter des scripts wrapper qui appellent les `rest_command` / scripts du package, ou modifier `mt-console-card.js` pour cibler `script.mediatracker_suivre`. |


## 1. Créer le jeton dans MediaTracker

1. Ouvrez l'adresse indiquée par `mediatracker_url` et connectez-vous.
2. Réglages → jetons d'API → créez un jeton nommé par exemple `Home Assistant`.
3. Cochez la portée **write** (elle inclut la lecture). Un jeton seulement `read` affiche le tableau de bord mais refuse les boutons.
4. Copiez le jeton `mt_…` tout de suite : il ne sera plus réaffiché. Il se révoque au même endroit.

## 2. Activer les packages

Dans `/config/configuration.yaml` de Home Assistant :

```yaml
homeassistant:
  packages: !include_dir_named packages
```

Créez le dossier `/config/packages/` s'il n'existe pas, et copiez-y `mediatracker.yaml`.

## 3. Secrets

Dans `/config/secrets.yaml`, ajoutez les deux clés. L'exemple complet est `homeassistant/secrets.yaml`.

- `mediatracker_url` : seule adresse de MediaTracker. Forme : `http://hôte:8080`, sans barre finale.
- `mediatracker_token` : le préfixe `Bearer ` fait partie de la valeur. Collez le jeton créé à l'étape 1.

```yaml
mediatracker_url: "http://hôte:8080"
mediatracker_token: "Bearer mt_…"
```

Pour changer de machine, modifiez seulement `mediatracker_url`, puis redémarrez Home Assistant.

## 4. Cartes HACS

Installez button-card, Mushroom, card-mod, auto-entities et layout-card (voir `HACS.md`), puis redémarrez Home Assistant une première fois pour charger les ressources. Sans layout-card, la vue `custom:grid-layout` ne s'affiche pas.

## 5. Redémarrer et vérifier les capteurs

Paramètres → Système → Redémarrer. Vérifiez ensuite Outils de développement → États : `sensor.mediatracker` doit exister. Son état est `ok`, `dégradé` ou `indisponible`. Les attributs (`downloads`, `queue`, `to_watch`, `upcoming`, `catalog`, `source_scan`, `health`, `disk`, `library_scan_at`, `update_available`, `update`, `digital_releases`, `airing_series`, `build`) viennent du même appel, toutes les 60 secondes.

Les capteurs dérivés (`sensor.mediatracker_adresse`, `sensor.mediatracker_sante`, `sensor.mediatracker_telechargements`, `sensor.mediatracker_progression`, `sensor.mediatracker_vitesse`, `sensor.mediatracker_file`, `sensor.mediatracker_episodes`, `sensor.mediatracker_films`, `sensor.mediatracker_nouveautes`, `sensor.mediatracker_disque`, `sensor.mediatracker_version`) ne font pas d'appel supplémentaire. `sensor.mediatracker_version` affiche la version et le commit court.

## 6. Créer le tableau de bord

1. Paramètres → Tableaux de bord → Ajouter un tableau de bord.
2. Ouvrez-le, passez en mode édition, menu (trois points) → Éditeur de configuration brut.
3. Remplacez tout le contenu par `dashboard.yaml`.
4. Enregistrez. Le profil en mode sombre se rapproche des maquettes. Laissez le type de vue sur `custom:grid-layout` : Panneau, Maçonnerie et Sections ignorent cette grille et réempilent les cartes.

En dessous de 840 px, layout-card empile les blocs dans l'ordre du téléphone : Santé / Disque / Version sur une ligne, Téléchargements, À voir, affiches, Suivre, file d'attente. À partir de 840 px, la même vue place trois colonnes en haut, les affiches de Sorties numériques et de Séries à l'antenne sur deux colonnes, et Suivre avec la file à droite. Les affiches font 76 px et restent alignées au début : elles ne s'étirent pas sur la largeur. Les tuiles Santé, Disque et Version passent alors les unes sous les autres. Un capteur d'affiche vide n'est pas dessiné.

Les petits boutons appellent `rest_command.mediatracker_verifier_sources`, `mediatracker_actualiser_nouveautes`, `mediatracker_scanner_bibliotheque`, `mediatracker_pause_telechargements` et `mediatracker_reprendre_telechargements`. « Mise à jour » et « Redémarrer » restent sous les tuiles d'état. Redémarrer demande confirmation.

**Affiches** : un appui ouvre la page TMDB. Un appui long suit le titre (et le met en file si MediaTracker ne répond pas). Sans URL `http` ou `https`, la carte est un bouton gris, jamais une carte image rouge. Un emplacement vide (pas de titre) reste masqué.

## 7. Suivre et file d'attente (NAS éteint)

`input_text.mediatracker_tmdb_id` et `input_select.mediatracker_type` (Film ou Série) sont dans le package. Le bouton appelle `script.mediatracker_suivre`.

- Champ vide, ou qui n'est pas un entier positif : notification « Indiquez un identifiant TMDB. », aucun appel.
- Sinon l'identifiant est écrit **avant** l'appel dans `input_text.mediatracker_file_attente`. Cette aide survit au redémarrage de Home Assistant.
- MediaTracker ne répond pas, réponse vide, ou erreur 5xx : l'identifiant reste `en attente`. Le message est « MediaTracker ne répond pas. L'identifiant reste en attente. » Le script donne une valeur par défaut à `resultat` avant de lire la réponse, pour ne plus planter sur `'resultat' is undefined`.
- Réponse 2xx avec un corps JSON : l'identifiant passe à `envoyé`. Il quitte la liste à réessayer, la ligne reste affichée.
- Réponse 4xx : la ligne reste `en attente`, le détail est notifié, et il n'y a pas de réessai automatique. Un nouvel appui sur Suivre relance cet identifiant.
- L'automatisation `mediatracker_file_attente_envoi` envoie les lignes encore à réessayer quand `sensor.mediatracker` vaut `ok` ou `dégradé`, au démarrage de Home Assistant, et toutes les cinq minutes. Un identifiant n'est retiré de ce lot qu'après confirmation.
- Douze identifiants au plus. Les lignes `envoyé` les plus anciennes cèdent la place en premier.

Le format stocké est `id:m|t:a|h|e`, séparé par des virgules (`m` film, `t` série, `a` à réessayer, `h` refusé, `e` envoyé). Ce n'est pas un secret.

La réponse d'un envoi confirmé est une notification : titre suivi, déjà suivi, ou l'erreur (jeton TMDB absent, identifiant inconnu). Un titre déjà suivi ne relance pas de téléchargement. Les fichiers déjà dans la bibliothèque sont rattachés.

## Notifications (webhook déjà en place)

Le tableau de bord ne remplace pas les notifications. La cible Home Assistant reste un webhook sans HMAC : `http://<hôte-ha>:8123/api/webhook/<id>`. L'identifiant dans l'URL est le secret. Corps inchangé : `title`, `message`, `poster_url`, et `episode` en objet (`id`, `season`, `number`, `title`, `path`). S'y ajoutent `event`, `media_id`, `tmdb_id`, `season`, `episode_number`, `url`, `level`.

Dans MediaTracker : Réglages → notifications → cible Home Assistant → collez cette URL. Cochez les événements voulus (`library.imported`, `download.failed`, `item.available`, `disk.low`, …).

Exemple d'automatisation pour un fichier déposé sur le NAS :

```yaml
alias: MediaTracker — fichier sur le NAS
description: Notification quand MediaTracker dépose un épisode ou un film.
triggers:
  - trigger: webhook
    allowed_methods:
      - POST
    local_only: true
    webhook_id: mediatracker
conditions:
  - condition: template
    value_template: "{{ trigger.json.event == 'library.imported' }}"
actions:
  - action: notify.mobile_app_telephone
    data:
      title: "{{ trigger.json.title }}"
      message: "{{ trigger.json.message }}"
      data:
        image: "{{ trigger.json.poster_url | default('', true) }}"
        tag: "mediatracker-{{ trigger.json.media.id }}-{{ trigger.json.episode.id | default('film') }}"
mode: queued
max: 10
```

`webhook_id` est le dernier segment de l'URL. `local_only: true` limite l'appel au réseau local.
