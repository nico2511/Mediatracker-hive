# Disque et sauvegardes

URL : `/reglages/disque`

## Espace disque

Carte **espace disque** (volumes données et configuration).

| Clé | Défaut | Plage | Rôle |
| --- | --- | --- | --- |
| `disk_min_free_gb` | 5 | 0–1024 | Garde-fou espace libre sur le volume des téléchargements (`0` = désactivé) |

Comportement :

- **Sous le seuil** : téléchargements en **pause**, notification « Espace disque » ; les jobs actifs passent en erreur `DISK_LOW` jusqu’au retour au-dessus du seuil.
- **Avant de lancer un nouveau téléchargement** (aria2) : le serveur exige l’espace libre **minimum réglé**, plus la taille estimée du fichier (avec une marge fixe), sinon le job reste en attente (`DISK_FULL`) sans démarrer aria2.
- Quand l’espace redevient suffisant, les téléchargements en pause pour cause de disque sont repris.

Le nombre de sauvegardes quotidiennes conservées se règle dans [Corbeille](corbeille.md) (`backup_keep`).

## Sauvegarde / restauration

Bloc **Sauvegarde / restauration** : liste des copies dans `/config/backups`, téléchargement d’un fichier `.db`, import avec aperçu et confirmation.

Voir [Sauvegarde](../sauvegarde.md).