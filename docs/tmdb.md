# TMDB

**TMDB** (*The Movie Database*) est un catalogue en ligne de films et séries. MediaTracker s’en sert en **lecture seule** pour afficher les affiches, les métadonnées et pour identifier vos fichiers sur le disque.

## Jeton API

MediaTracker appelle l’API TMDB v4 avec un **jeton de lecture** (`tmdb_read_token`) :

1. Créez un compte sur [themoviedb.org](https://www.themoviedb.org/settings/api).
2. Générez un jeton API en mode **lecture** (Read Access Token).
3. Saisissez-le dans l’assistant **TMDB** au premier démarrage, ou plus tard dans **Réglages**.

Le jeton est **chiffré** en base de données. Vous pouvez aussi le passer une fois en variable d’environnement au tout premier boot : `TMDB_READ_TOKEN` (voir [`.env.example`](../.env.example)).

## Dans l’application

| Écran | Rôle |
| --- | --- |
| **Accueil** (`/`) | Tableau de bord personnel et rangées de nouveautés TMDB (France, français) |
| **Derniers films** (`/nouveautes/films`) | Parcourir les films (cinéma, streaming, supports physiques) |
| **Dernières séries** (`/nouveautes/series`) | Séries et diffusions à l’antenne |
| **Suivre** | Rechercher un titre et l’ajouter à votre liste de suivi |
| **Médias** (`/medias`) | Liste de vos fiches : chargement progressif, affiches et bannières via le cache local TMDB, badges Hive chargés ensuite (sans bloquer la grille) |
| Fiche média | Métadonnées, saisons, estimation de disponibilité pour les films ; bloc Hive unifié si Hive est actif |

Les anciennes URL d’essai `/tmdb`, `/tmdb/films` et `/tmdb/series` redirigent vers les écrans ci-dessus.

## Fiche non suivie

Lorsque vous avez arrêté le suivi d’un titre mais que la fiche reste en base (fichiers sur le disque, historique, etc.) :

- Le bandeau propose **Suivre à nouveau** pour réactiver le suivi sans repasser par **Suivre**.
- Les actions avancées (nettoyage, suppression de fichiers, etc.) sont regroupées sous **Plus d’actions** ; la zone de suppression définitive de la fiche n’apparaît pas sur cet écran épuré.
- Le partage **Hive** reste accessible dans le même bloc que pour une fiche suivie, avec l’état adapté (fichier présent ou non).

## Profils de qualité

Les critères de résolution, langue et mots à exclure pour **choisir un fichier** parmi les liens d’une source se règlent dans **Réglages → Profils de qualité** — voir [reglages/qualite.md](reglages/qualite.md).

## Corriger l’identifiant TMDB d’une série

Chaque fiche média est liée à un **identifiant numérique TMDB**. Si vous avez suivi la mauvaise série (homonyme, mauvaise saisie), vous pouvez le corriger depuis la fiche.

### Première saisie

Lorsque la fiche n’avait **pas encore** d’identifiant TMDB, la nouvelle valeur est enregistrée immédiatement.

### Remplacement sur une série qui en avait déjà un

Le serveur affiche d’abord un **aperçu** des changements (saisons, épisodes, fichiers concernés). Rien n’est modifié tant que vous n’avez pas confirmé.

**Où aller :** fiche média → **Réglages** → **Identification** → **Corriger l’identifiant**.

**Trois choix possibles :**

| Action | Effet |
| --- | --- |
| **Reconstruire les épisodes** | Les lignes d’épisodes de l’ancien catalogue qui n’existent plus sous le nouvel identifiant sont retirées de la fiche. Les **fichiers sur le disque ne sont ni supprimés ni déplacés** ; ils apparaissent dans la bibliothèque en zone **À réviser**, avec la note « À réassocier », jusqu’à ce que vous les rattachez manuellement à un épisode. |
| **Conserver les épisodes** | Comportement plus permissif : les épisodes hors nouveau catalogue TMDB restent listés sur la fiche. |
| **Annuler** | Aucune modification. |

**À savoir :**

- Un simple **rafraîchissement** des métadonnées TMDB (même identifiant) ne déclenche pas ce flux.
- Les fichiers d’un autre titre portant le même numéro de saison/épisode (S01E05, etc.) **ne sont pas** rattachés automatiquement au nouveau catalogue.
- Tant qu’un fichier porte la note « À réassocier », le scan bibliothèque **ne le rattache pas** tout seul.

Pour un **film**, le changement d’identifiant reste une opération directe (pas d’aperçu 409).
