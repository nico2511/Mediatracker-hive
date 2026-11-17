# Médias

**Médias** (`/medias`) est l’écran qui réunit toutes vos **fiches** films et séries suivies ou conservées dans MediaTracker : affiches, état de téléchargement, progression de visionnage et raccourcis vers la fiche détaillée ou Jellyfin.

Pour le jeton TMDB, le suivi depuis **Suivre** et la correction d’identifiant, voir [TMDB](tmdb.md). Pour le partage entre instances, voir [Hive](hive.md).

## À la une

En haut de la page, un bandeau **À la une** met en avant une fiche parmi celles **actuellement affichées** (filtres et recherche pris en compte) :

- priorité à une **série en cours** (épisodes vus, mais pas tous) ;
- sinon la première carte de la liste filtrée.

Le bandeau propose **Fiche** (vers `/medias/<id>`) et, si Jellyfin est lié, **Lire sur Jellyfin**.

## Chargement de la liste

La grille charge l’intégralité de votre collection par **pages** via `GET /api/v1/media` (200 fiches par page, curseur de pagination). Le compteur **Chargement… N titres reçus** augmente au fur et à mesure.

- Hors ligne (mode PWA), l’application peut afficher la **dernière visite** en cache ; les affiches ne sont pas conservées hors ligne.
- Si **Hive** est activé, des pastilles Hive sur les cartes se chargent **après** la grille, sans bloquer l’affichage des affiches.

Les statistiques sous le titre indiquent le nombre total de titres, de séries et de films. Lorsqu’un filtre ou une recherche est actif, une ligne **N affichés** précise le sous-ensemble visible.

## Filtres d’état (boutons)

Une rangée de boutons restreint la liste côté interface (sans nouvel appel API) :

| Bouton | Effet |
| --- | --- |
| **Tous** | Aucun filtre d’état |
| **Disponibles** | Fiches dont la disponibilité est « disponible » (fichier obtenu via le suivi) |
| **En attente** | En attente de téléchargement, pas encore vues |
| **Sur le disque** | Fichier présent sur le NAS (y compris partiel pour une série) |
| **En pause** | Suivi en pause |
| **Suivi par Jellyfin** | Fiches issues de l’identification bibliothèque (`follow_origin` identification) |

Sur **Suivi par Jellyfin**, l’action **Ne plus suivre ces fiches** appelle `POST /api/v1/media/unfollow-identified` : les fiches identifiées passent en non suivies, les fichiers restent en place.

## Recherche, filtres avancés et tri

**Recherche** : filtre instantané sur le **titre** (insensible à la casse, locale française).

**Tri** :

- **Alphabétique** (défaut) ;
- **Récents** (date d’ajout décroissante).

**Filtres avancés** (menus **Suivi**, **Vu**, **Type**, **Origine**) :

| Filtre | Valeurs |
| --- | --- |
| Suivi | Tous, Suivis, Non suivis |
| Vu | Tous, Vus, En cours (progression > 0 sans « vu » global), Non vus |
| Type | Tous, Séries, Films |
| Origine | Toutes, Ajout manuel, Import Jellyfin, Identifiés bibliothèque, À identifier TMDB |

Sur mobile, le panneau **Filtres** regroupe ces réglages ; des **pastilles** rappellent les filtres actifs, avec **Réinitialiser** pour tout effacer.

## Densité de la grille

**Aérée** ou **Dense** : le choix est mémorisé dans le navigateur. En mode dense, l’année et le type sur chaque carte sont allégés.

## Cartes et pastilles

Chaque carte affiche l’affiche TMDB (cache local), le titre, le type (Film / Série), l’année (sauf en dense), puis :

**Pastille d’état** (priorité dans cet ordre) :

| Pastille | Signification |
| --- | --- |
| En pause | Suivi suspendu |
| Vu | Marqué comme vu |
| Sur le disque | Fichier présent |
| Partiel | Série incomplète sur disque (libellé personnalisé possible) |
| Disponible | Fichier obtenu via le suivi |
| Pas encore disponible | Aucune source trouvée pour l’instant |
| En attente | En attente de téléchargement |

**Icônes** complémentaires : **Suivi**, **Sur le disque**.

**Pastilles Hive** (si Hive actif, sur les fiches visibles) : par exemple **Partagé**, **Préparation au partage**, **En transfert**, **Mode tirage**, etc.

Un clic sur la carte ouvre l’**aperçu rapide** (voir ci-dessous). Le lien **Fiche** sur la carte mène à `/medias/<id>`.

## Menu de carte

Chaque carte expose un menu d’actions :

- **Appui long** (tactile) ou **clic droit** sur l’affiche ;
- touche **Menu** au clavier (indication d’accessibilité sur la carte).

Entrées typiques sur la liste Médias :

- **Fiche** ;
- **Lire sur Jellyfin** (si une URL Jellyfin est connue).

En mode **Sélectionner**, le lien **Fiche** sur la carte est masqué ; un clic sur la carte coche ou décoche la fiche.

## Aperçu rapide

Un clic sur l’affiche (hors sélection multiple) ouvre un panneau **aperçu** :

- synopsis, année, type ;
- pour une fiche **non suivie** : **Suivre** (avec choix des saisons et politique des nouveaux épisodes pour une série), lien **TMDB**, **Fiche** ;
- pour une fiche **suivie** : **Fiche**, **Lire sur Jellyfin**, **Source** (si applicable), **Fermer**.

Données complétées via `GET /api/v1/media/<id>` lorsque l’aperçu s’ouvre.

**Échap** ferme l’aperçu (ou le sous-dialogue de suivi).

## Fiche média (`/medias/<id>`)

La fiche charge `GET /api/v1/media/<id>` et, pour une série, `GET /api/v1/media/<id>/items` (saisons et épisodes).

### En-tête commun

- Affiche, titre, année, synopsis ;
- pour une série : statut TMDB, nombre de saisons, barre **X vu sur Y**, prochain épisode ;
- pastilles **Suivi**, état fichier ou **Pas encore disponible** (film) ;
- liens **TMDB** / **Jellyfin**, **Bande-annonce** (si TMDB fournit une clé YouTube).

### Actions principales (fiche suivie)

Le bloc **Suivi** / **Actions** (composant partagé film et série) regroupe :

- **Action principale** selon l’état : *Vérifier maintenant*, *Rechercher maintenant*, *Retélécharger*, *Restaurer* (fichier en corbeille), etc. ;
- **Mettre en pause** / **Reprendre** ;
- menu **Plus d’actions** : vérification manuelle, pour une série *Marquer la série comme vue / non vue*, **Ne plus suivre…** ;
- interrupteurs **Garder** (film ou série), **Téléchargement automatique** (série) ;
- section **État** repliable : progression, fichier, dernière et prochaine vérification, message du moteur de recherche.

Fiche **série** : mise en page dédiée (carte Suivi mobile, barre d’actions bureau).

### Fiche film — fichier

Hors « pas encore disponible », le panneau **Film** détaille le fichier unique : statut, nom, actions **Garder**, marquer vu, retélécharger, forcer le téléchargement, suppression vers corbeille, partage Hive si applicable.

### Fiche série — saisons et épisodes

Section **Saisons et épisodes** (série suivie ou fiches avec fichiers restants) :

- chaque saison se **déplie** ; compteurs vus, fichiers sur disque, épisodes au catalogue ;
- par saison : **Garder**, menu (marquer saison vue/non vue, supprimer la saison, forcer la saison, partage Hive en masse) ;
- par épisode : code SxxExx, statut (téléchargé, en cours, absent…), nom de fichier, date de diffusion, puce Hive ;
- **Garder** par épisode (désactivé si la série ou la saison est déjà en « Garder ») ;
- menu épisode : vu/non vu, vu jusqu’à cet épisode, restaurer, retélécharger, télécharger, forcer, supprimer, partage Hive, etc.

### Réglages de la fiche

Le repli **Réglages de cette fiche** (`?reglages=ouverts` dans l’URL l’ouvre) contient notamment : profil de qualité et repli, ordre des sources, identification TMDB, association dossier bibliothèque, Jellyfin (séries), maintenance (liens morts, réparation). Voir [Profils de qualité](reglages/qualite.md) et [TMDB — Corriger l’identifiant](tmdb.md#corriger-lidentifiant-tmdb-dune-série).

### Fiche non suivie

Bandeau **Suivre à nouveau** à la place des actions de suivi actif. Les opérations avancées sont sous **Plus d’actions** (garder, réglages, TMDB, changement d’identifiant, suppression fichiers ou fiche). La zone **Suppression et fin du suivi** en bas n’apparaît pas sur l’écran épuré non suivi — détail dans [TMDB — Fiche non suivie](tmdb.md#fiche-non-suivie).

### Zone danger (fiche suivie)

Repli **Suppression et fin du suivi** :

- **Ne plus suivre** (option corbeille pour les fichiers) ;
- **Supprimer tous les fichiers** / **le film** (corbeille, fiche conservée) ;
- **Supprimer la fiche…** : dialogue avec choix **corbeille** ou **conserver sur le disque** (`DELETE /api/v1/media/<id>?trash=true|false`).

## Sélection multiple et suppression en masse

Sur **Médias** (`/medias`), le bouton **Sélectionner** permet de cocher plusieurs fiches **parmi celles affichées** (filtres et recherche respectés), puis **Supprimer les fiches**.

- Barre d’outils : compteur, **Tout sélectionner** (visible uniquement), **Annuler**, **Supprimer les fiches** ;
- **Échap** quitte le mode sélection ou ferme le dialogue de confirmation ;
- la confirmation appelle `DELETE /api/v1/media/bulk` avec `trash: false` : les fiches, épisodes, liens et historique associés sont retirés ; les téléchargements en cours sont **annulés** ; le journal des renommages est **conservé** ;
- en sélection multiple, l’interface précise que **les fichiers sur le disque ne sont pas touchés** (aucun envoi à la corbeille). Les dossiers restent dans la bibliothèque physique ;
- depuis **une seule fiche**, le dialogue de suppression propose le choix entre corbeille et conservation sur disque.

Le mode sélection est indisponible hors ligne lorsque les actions serveur sont désactivées.
