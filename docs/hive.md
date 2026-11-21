# Hive

**Hive** est le mode de **partage de catalogue** et de **téléchargement entre instances** MediaTracker qui appartiennent aux mêmes **ruches** (groupes de membres approuvés).

## Activation

**Réglages → Hive** (`/reglages/hive`) :

1. Activer l’interrupteur **Hive** (désactivé par défaut).
2. À la première activation, choisir un **nom d’instance** (lié à l’identité cryptographique ; ne change pas sans réinitialisation d’identité).
3. Renseigner l’**adresse publique** HTTPS (ou HTTP) joignable par les autres instances — par exemple `https://mtr.exemple.fr` — **sans** identifiants dans l’URL.

L’entrée de menu **Hive** n’apparaît que lorsque Hive est activé.

## Ruches et membres

- **Créer une ruche** : depuis Réglages Hive ou la navigation Hive.
- **Inviter** : lien d’invitation généré par l’application ; page **Rejoindre** (`/hive/rejoindre`).
- **Membres** (`/hive/ruches/<id>/membres`) : validation des demandes (créateur de la ruche), quotas, exclusion. Chaque membre affiche une pastille de version MediaTracker (connue via le gossip pair, sans toucher à la liste signée des membres) : **à jour** si le protocole Hive annoncé est au moins égal au vôtre, **ancienne version** sinon, **version inconnue** pour un pair qui n’a pas encore annoncé.

Le mode **tirage seul** (`pull_only`) : l’instance peut recevoir des fichiers sans en envoyer (affiché dans l’interface si applicable).

### Réinitialiser le catalogue (créateur)

Le créateur dispose du bouton **Réinitialiser le catalogue** dans **Réglages → Hive** (bloc de chaque ruche, à côté de Membres) et, sur la page **Membres**, d’une zone de danger dédiée. L’action exige de retaper le nom court de la ruche (`Ruche` + les huit premiers caractères de l’identifiant hexadécimal).

Effet :

- Le créateur incrémente une **époque de catalogue** (`catalog_epoch`) incluse dans la **liste des membres signée** (avec horodatage optionnel `catalog_reset_at`), republie cette liste, puis purge localement les entrées catalogue et les signaux de la ruche avant de republier ses partages manuels déjà actifs.
- Chaque membre, en recevant une liste dont l’époque est supérieure à la sienne, applique la même purge locale (catalogue, signaux, vecteurs de version), republie ses partages existants, et **ignore** tout gossip catalogue ou signal signé d’une époque antérieure.
- Les **fichiers**, **fiches médias**, **membres**, réglages d’exclusion/partage par fichier et **téléchargements en cours** ne sont pas modifiés.

API admin : `POST /api/v1/hive/ruches/{id}/catalog-reset` avec `{ "confirm_name": "<nom court>" }`. Les champs `catalog_epoch` et `catalog_reset_at` sont aussi exposés sur `GET /api/v1/hive/ruches/{id}/detail`.

Les entrées et signaux publiés localement portent désormais le champ signé `catalog_epoch` (absent ou invalide = 0). La capacité gossip `catalog_epoch` est annoncée via `instance` ; l’absence de cette capacité chez un pair ancien n’empêche pas la synchronisation.

### Gossip et version d’instance

Lors du `POST /hive/ruche/{id}/gossip/sync`, chaque instance émet toujours un objet `instance` (en plus du vecteur de catalogue et, le cas échéant, de l’instantané de charge `load`) : version d’affichage, numéro de protocole Hive et capacités déclarées. Ces données sont mises en cache localement pour l’interface ; elles ne remplacent pas la liste de membres signée.

## Catalogue et partage

- **Catalogue Hive** : œuvres partagées par les pairs ; bouton **Suivre** côté récepteur.
- **Repartage automatique** : option **Repartager automatiquement les fichiers reçus** (`hive_catalog_auto_share`, **désactivée** par défaut).
- **Accueil** : section **Dernières dispos Hive** lorsque des entrées existent.
- Sur une **fiche média**, un seul bloc **Hive** regroupe partage, exclusions par ruche et état de préparation.
- Sur une **fiche série**, ce bloc affiche une ligne **Partage** (`x/y épisodes partagés`, lien **Gérer**) et, si des épisodes figurent dans **Mes demandes**, une ligne **Demande** (`n épisodes demandés · m en transfert`) avec **Voir mes demandes** et **Ne plus demander**, qui retire d’un coup toutes les demandes en attente ou en transfert de la série. Sur une fiche film, la demande garde sa carte dédiée.

Avant qu’un fichier soit visible des pairs, l’instance calcule une **empreinte** (empreinte SHA par blocs du fichier) via un processus dédié dans le conteneur.

## Demandes des autres

Navigation **Hive** : onglets **Catalogue**, **Mes demandes**, **Demandes des autres**.

**Mes demandes** regroupe vos suivis par fiche (une carte par série ou par film). Les séries se déplient pour lister chaque épisode demandé avec son état ; les fiches avec un transfert en cours remontent en tête.

**Demandes des autres** affiche deux sections : **Vous l’avez** (partage à valider ou refus) et **Personne ne l’a encore** (recherches sans possesseur). La pastille de l’onglet est la somme des deux listes. Les URL `/hive/recues` et `/hive/recherche` redirigent vers cet onglet. Deux interrupteurs en tête de page correspondent à `hive_auto_accept_requests` et `hive_auto_take_wanted`. Quand ce dernier est activé, l’instance prend en charge automatiquement jusqu’à trois recherches de la section **Personne ne l’a encore** par passage (à l’activation du réglage, à l’échéance gossip et à l’ouverture de l’onglet), en réutilisant la même logique que la prise en charge manuelle.

- Rien n’est partagé tant que vous ne l’avez pas décidé ; une **demande** prévient les membres qui possèdent déjà le fichier (demandes **anonymes** côté ruche).
- **Vous l’avez** affiche une seule ligne par épisode ou film demandé, même si plusieurs membres ou plusieurs versions sont demandés ; **Accepter** ou **Refuser** s’applique à toutes les demandes regroupées sur cette ligne.
- Quand la version demandée change (qualité ou langue), l’ancienne demande est retirée de la ruche avant la publication de la nouvelle.
- Si aucun pair ne l’a, le titre peut figurer dans **Personne ne l’a encore** pour qu’un autre membre se le procure puis le partage.
- **Réglages → Hive → Demandes à la ruche** : comportement quand la recherche locale ne trouve rien (`hive_request_on_miss` : automatique, me demander, jamais — avec nuances selon la présence d’une clé AllDebrid), plus les interrupteurs d’acceptation automatique et de prise en charge automatique des recherches.
- **Réglages → Hive → Source de téléchargement → Ruche seule** (`hive_only`) : le téléchargement automatique et les sondages n’utilisent que Hive ; les sources distantes restent testables à la main dans Réglages → Sources.

## Téléchargements

Hive est une **source** comme les autres dans l’ordre de téléchargement (`hive` dans `download_source_order`). Lorsqu’un fichier est disponible chez un pair :

1. MediaTracker obtient un **grant** de lecture temporaire, signé par le pair.
2. **aria2** (téléchargeur dans le conteneur) récupère le fichier en HTTP avec l’en-tête Hive prévu.
3. Le fichier apparaît dans **Téléchargements** puis est rangé comme un téléchargement AllDebrid classique.

Il n’y a **pas** d’écran de transferts Hive séparé : tout passe par la file **Téléchargements** habituelle.

### Choix manuel d’une version avec fichier déjà présent

Lorsque vous demandez **explicitement** une version dans le catalogue Hive (y compris une version hors profil), alors qu’un fichier occupe déjà le créneau sur le disque :

- la demande n’est **pas** refusée ;
- le nouveau fichier est rangé **à côté** de l’existant, avec le suffixe Jellyfin ` - {version}` (ex. `Titre (2026) - 1080p VOSTFR.mkv`) ;
- le fichier déjà lié à la fiche **reste** celui de l’épisode ou du film ; l’autre copie est enregistrée dans l’index bibliothèque pour ne pas être traitée comme orpheline ;
- il n’y a **pas** de départage qualité ni de passage à la corbeille.

Les téléchargements **automatiques** et le **remplacement proposé** après un repli de profil conservent le comportement habituel (un seul fichier retenu).

### Bon samaritain (recherches sans fichier)

Si vous prenez en charge une entrée **Personne ne l’a encore** pour un autre membre, la recherche locale (sources et Hive) n’utilise que le **profil de qualité par défaut** de l’instance, **sans** enchaîner les profils de repli.

### Visibilité des recherches sans fichier

Une entrée **Personne ne l’a encore** est masquée dès qu’un fichier est présent sur le créneau concerné, **quelle que soit la version** (y compris une copie secondaire en multi-versions).

Si le pair est injoignable ou **occupé** (limite de transferts, fenêtre horaire, débit saturé), MediaTracker peut **classer** les sources disponibles d’après les instantanés de charge reçus des pairs, choisir un pair moins chargé, puis **basculer** vers un autre pair ou une autre source de l’ordre **Réglages → Sources**.

Les pairs publient périodiquement un instantané : transferts sortants en cours, plafond de parallélisme, plafond de débit d’envoi, débit disponible estimé, indicateur **occupé**. Les données reçues lors des échanges Hive sont conservées un temps limité ; les entrées trop anciennes sont traitées comme inconnues.

## Limites de débit et fenêtre horaire

Toujours dans **Réglages → Hive** :

| Réglage | Plage | Rôle |
| --- | --- | --- |
| `hive_transfer_parallel` | 1–16 | Téléchargements simultanés depuis les pairs (défaut 2) |
| `hive_transfer_send_bps` | 0–2e9 | Débit d’envoi vers les pairs (0 = illimité) |
| `hive_transfer_recv_bps` | 0–2e9 | Débit de réception |
| `hive_transfer_window_start` / `end` | minutes dans la journée | Fenêtre horaire autorisée |

## Identité illisible

Si la clé privée Hive ne peut plus être lue, l’interface bloque la plupart des réglages Hive jusqu’à **Réinitialiser l’identité** (bouton dans Réglages ou `POST /api/v1/hive/identity/reset`).

## Plusieurs instances sur un même réseau

Chaque instance doit avoir :

- une **`SECRET_KEY` distincte** ;
- des volumes **`/config`** et **`/data`** séparés ;
- une **adresse publique** distincte (souvent derrière un proxy inverse).

Exemple de fichier Compose : [exemples/docker-compose-deux-instances-hive.yml](exemples/docker-compose-deux-instances-hive.yml).

Pour des scénarios avancés (proxy multi-domaines, sandboxes), voir aussi [exemples/docker-compose-hive-sandbox.example.yml](exemples/docker-compose-hive-sandbox.example.yml).

## Suivi dans Santé

**Santé → Performance** : encart **Ruche** avec vos envois en cours, débit sortant actuel, décompte de pairs joignables / occupés / inconnus, puis le détail par ruche (barre de charge, état libre / occupé / inconnu).
