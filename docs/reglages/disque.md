# Disque et sauvegardes

URL : `/reglages/disque`

Page en trois blocs : **espace disque**, **téléchargements incomplets et orphelins**, **sauvegardes**.

## Espace disque

- **Résumé** : volumes (total / utilisé / libre), espace libre global et date du dernier calcul, avec bouton **Actualiser**.
- **Détail des dossiers** : repliable par défaut ; chemins et tailles par dossier (bibliothèque, téléchargements, etc.).
- **Espace libre minimum (Gio)** :

| Clé | Défaut | Plage | Rôle |
| --- | --- | --- | --- |
| `disk_min_free_gb` | 5 | 0–1024 | Garde-fou espace libre sur le volume des téléchargements (`0` = désactivé) |

Comportement :

- **Sous le seuil** : téléchargements en **pause**, notification « Espace disque » ; les jobs actifs passent en erreur `DISK_LOW` jusqu’au retour au-dessus du seuil.
- **Avant de lancer un nouveau téléchargement** (aria2) : le serveur exige l’espace libre **minimum réglé**, plus la taille estimée du fichier (avec une marge fixe), sinon le job reste en attente (`DISK_FULL`) sans démarrer aria2.
- Quand l’espace redevient suffisant, les téléchargements en pause pour cause de disque sont repris.

La carte **espace disque** de la page **Santé** reprend les mêmes données avec un affichage plus détaillé.

## Téléchargements incomplets et orphelins

- Nombre de fichiers incomplets listés par l’API disque.
- **Mettre à la corbeille** : lien vers la Bibliothèque (filtre corbeille) uniquement s’il existe des fichiers éligibles (`incomplete_trash_count` ou orphelins).
- Sinon : message **Rien à mettre à la corbeille**, avec éventuellement le décompte des fichiers récents gardés et leur date limite.
- Liste des fichiers incomplets (nom, taille, état du job).

## Sauvegardes

- Dossier des copies quotidiennes (`backups_dir` côté serveur).
- **Sauvegardes quotidiennes conservées** : valeur lue depuis le réglage `backup_keep` ; la modification se fait dans [Corbeille](corbeille.md) (lien **Modifier**).
- Bloc **Sauvegarde / restauration** : liste des copies, téléchargement d’un fichier `.db`, import avec aperçu et confirmation.

Voir [Sauvegarde](../sauvegarde.md).