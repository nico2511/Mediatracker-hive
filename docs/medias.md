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
| **Lien trouvé** | Fiches dont `availability` vaut `available` : une source a trouvé un lien, sans fichier sur le disque |
| **En attente** | En attente de téléchargement, pas encore vues (hors « pas encore disponible ») |
| **Sur le disque** | Fichier présent sur le NAS (y compris partiel pour une série) |
| **En pause** | Suivi en pause |

Un bouton **?** à côté de chaque libellé affiche une phrase d’aide ; sur téléphone, un encadré sous le filtre d’état actif résume le critère.

Le bouton **Sélectionner** est à part de cette rangée (à droite sur grand écran, sur sa propre ligne sur téléphone).

Lorsque le filtre **Origine** vaut **Identifiés bibliothèque**, l’action **Ne plus suivre ces fiches** appelle `POST /api/v1/media/unfollow-identified` : les fiches identifiées passent en non suivies, les fichiers restent en place. Une ancienne URL ou un ancien état `etat=identified` est traduit en `origine=identification`.

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

Chaque carte affiche l’affiche TMDB (cache local), le titre, le type (Film / Série), l’année (sauf en dense), puis une ligne **★ note TMDB** (une décimale, rien si la fiche n’a pas de note), puis :

**Pastille d’état** (priorité dans cet ordre) :

| Pastille | Signification |
| --- | --- |
| En pause | Suivi suspendu |
| Vu | Marqué comme vu |
| Sur le disque | Fichier présent |
| Partiel | Série incomplète sur disque (libellé personnalisé possible) |
| Disponible | Source a trouvé un lien (`availability` disponible), sans fichier sur le disque |
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
- ligne **★ TMDB X/10 · N votes** (ou « 1 vote ») lorsque la fiche possède une note enrichie — rien si zéro vote ou note nulle en base ;
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

### Fiche film — dates TMDB par région

Sur une fiche film, sous le résumé, MediaTracker peut afficher les dates **en salle**, **numérique** et **physique** pour la **région du catalogue TMDB** (Réglages → Catalogue TMDB) et pour les **États-Unis**, lorsque TMDB les fournit. La date qui déclenche la recherche des sources est mise en évidence, avec une courte phrase du type *Recherche des liens à partir du …*. L’option **Déclencher dès la première sortie numérique US** (Réglages → Réglages de scan) compare uniquement les sorties numériques région / US et retient la plus précoce.

### Fiche film — fichier

Hors « pas encore disponible », le panneau **Film** détaille le fichier unique : statut, nom, actions **Garder**, marquer vu, **Retélécharger** (remplace le fichier sur ordinateur), suppression vers corbeille. Le bloc **Hive** et les pastilles de partage sur la fiche film s’affichent lorsque le fichier est **présent** sur le disque, pas seulement marqué téléchargé.

### Fiche série — saisons et épisodes

Section **Saisons et épisodes** (série suivie ou fiches avec fichiers restants) :

- chaque saison se **déplie** ; compteurs vus, fichiers sur disque, épisodes au catalogue ;
- par saison : **Garder**, menu (marquer saison vue/non vue, supprimer la saison, forcer la saison, partage Hive en masse) ;
- par épisode : code SxxExx, statut (téléchargé, en cours, absent…), nom de fichier, date de diffusion, pastilles Hive (**Partagé**, **Non partagé**, **Empreinte**, **Reçu via Hive**, **Entrant** / **Transfert entrant**, **Envoi** / **Transfert sortant**) selon l’état réel du fichier et du partage ; **Partagé** et **Envoi** peuvent coexister pendant un envoi ; un fichier reçu puis reparatagé n’affiche que **Partagé** ; compteur de saison `x/y partagés` basé sur les fichiers présents (corbeille et réception en cours exclus) ;
- **Garder** par épisode (désactivé si la série ou la saison est déjà en « Garder ») ;
- menu épisode (appui long / clic droit, sans ⋯) : vu/non vu, vu jusqu’à cet épisode, une seule entrée **Partager** ou **Ne plus partager**, choisir les ruches, restaurer, **Retélécharger** (téléphone) / **Retélécharger (remplace le fichier)** (ordinateur) si le fichier est déjà sur disque, télécharger, supprimer, etc.

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
- lorsque **Hive** est activé : **Partager sur Hive** et **Ne plus partager** s’ajoutent à la barre (**ordinateur**). Sur **téléphone**, **Partager** et **Annuler** restent visibles ; **Ne plus partager**, **Tout sélectionner** et **Supprimer les fiches** passent dans le menu **Plus** ;
- **Partager sur Hive** ouvre une confirmation listant les fiches sélectionnées et rappelle qu’une série ne partage que les épisodes présents sur disque (les titres sans fichier sont ignorés). **Ne plus partager** agit sans confirmation ;
- le résultat est affiché en nombre de **fiches** (une série compte pour 1), avec le décompte des titres sans fichier ignorés ; une fiche en **mode tirage** (instance qui ne publie pas) est **ignorée avec sa raison**, pas comptée en échec ; pour un partage réussi, un rappel précise que seuls les épisodes sur disque ont été pris en compte sur les séries ; la bannière de résultat peut être fermée ;
- l’action groupée appelle `PATCH /api/v1/hive/media/bulk-sharing` (`exclude_all: false` pour partager, `true` pour arrêter) ;
- **Échap** quitte le mode sélection ou ferme le dialogue de confirmation ;
- la confirmation appelle `DELETE /api/v1/media/bulk` avec `trash: false` : les fiches, épisodes, liens et historique associés sont retirés ; les téléchargements en cours sont **annulés** ; le journal des renommages est **conservé** ;
- en sélection multiple, l’interface précise que **les fichiers sur le disque ne sont pas touchés** (aucun envoi à la corbeille). Les dossiers restent dans la bibliothèque physique ;
- depuis **une seule fiche**, le dialogue de suppression propose le choix entre corbeille et conservation sur disque.

Le mode sélection est indisponible hors ligne lorsque les actions serveur sont désactivées.

## Masquer une fiche

Sur une fiche (`/medias/<id>`), le menu **Plus d'actions** (⋯) propose **Masquer la fiche** au-dessus des actions destructives. La fiche disparaît de **Médias**, de l'**Accueil** et du rapprochement des **suggestions catalogue** ; fichiers, historique « vu », partage Hive et liens Jellyfin restent en place.

- Si la fiche est **suivie**, une confirmation rappelle que le **suivi sera arrêté** (sans mettre les fichiers à la corbeille). **Réafficher** ne relance pas le suivi.
- Un bandeau **Fiche masquée · Annuler** permet d'annuler juste après l'action ; plus tard, **Réafficher** sur la fiche ou depuis la liste.

Sur **Médias**, la puce **Masquées N** (œil barré) n'apparaît que s'il existe au moins une fiche masquée. Elle affiche les cartes estompées avec la date de masquage et **Réafficher**. L'API utilise `POST /api/v1/media/<id>/hide` et `/unhide` ; la liste accepte `include_hidden=true` pour les inclure.
