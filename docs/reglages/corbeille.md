# Corbeille

URL : `/reglages/corbeille`. L’URL `/reglages/nettoyage` redirige vers cette page.

Section **Stockage** : contenu de la corbeille, rétention, suppression automatique du vu, incomplets, conservation des sauvegardes et de la liste des téléchargements, renvoi vers la purge AllDebrid si le compte est connecté.

## Contenu

Liste des fichiers en corbeille : **Restaurer**, **Supprimer** (définitif), **Vider la corbeille** (avec confirmation).

## Durée en corbeille

| Clé | Défaut | Plage | Rôle |
| --- | --- | --- | --- |
| `trash_retention_days` | 7 | 1–90 | Jours avant purge définitive des fichiers en corbeille |
| `trash_dir` | `/data/.trash` | chemin | Dossier corbeille (même volume que les bibliothèques) |

L’interface affiche le chemin **en vigueur** et un avertissement si les volumes empêchent les renommages atomiques.

## Suppression automatique du vu

| Clé | Défaut | Plage | Rôle |
| --- | --- | --- | --- |
| `auto_delete_enabled` | true | bool | Suppression auto du contenu vu |
| `auto_delete_days` | 14 | 1–365 | Délai après visionnage |
| `cleanup_notice_hours` | 24 | 1–168 | Préavis notification |

Comportement affiché : le contenu vu part à la corbeille après le délai et le préavis ; le suivi n’est pas requis ; une saison n’est retirée que si elle est entièrement vue. Une fiche peut imposer son propre délai ou être marquée **Garder**.

Bouton **Aperçu du prochain passage** : listes *Sera supprimé* et *Annoncé, pas encore supprimé*.

## Téléchargements incomplets

| Clé | Défaut | Rôle |
| --- | --- | --- |
| `incomplete_cleanup_enabled` | false | Après aperçu dans **Bibliothèque** (onglet *Nettoyage*) |

Quand c’est activé : orphelins déjà en bibliothèque, fichiers minuscules et contrôles aria2 peuvent partir à la corbeille ; les autres incomplets restent le nombre de jours indiqué dans **Durées fixes** ci-dessous.

## Conservation

| Clé | Défaut | Plage | Rôle |
| --- | --- | --- | --- |
| `backup_keep` | 7 | 1–90 | Sauvegardes SQLite quotidiennes conservées (`/config/backups`) |
| `downloads_completed_action` | `archive` | `archive` / `remove` | Entrée dans la liste Téléchargements après dépôt (fichier conservé en bibliothèque) |

## Durées fixes

Conservation automatique **côté serveur**, affichée en lecture seule dans le bloc **Durées fixes** (non modifiable dans l’interface). API : `GET /api/v1/settings/fixed-retention`.

| Élément (libellé UI) | Rôle |
| --- | --- |
| Journal des événements | Purge des entrées du journal au-delà de la fenêtre affichée |
| Historique des états des sources | Purge des changements d’état Wawacity, Comet, hébergeurs, etc. |
| Métriques de performance | Purge des points d’historique CPU/RSS |
| Téléchargements incomplets (avant nettoyage) | Délai avant proposition de corbeille pour un incomplet sans téléchargement actif |

Les valeurs exactes (en jours) sont celles affichées sur la page.

## AllDebrid

Bloc **AllDebrid** : lien vers [AllDebrid](alldebrid.md) pour la purge manuelle — **uniquement si le compte AllDebrid est connecté** (sinon bloc absent).