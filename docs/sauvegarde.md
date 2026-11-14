# Sauvegarde et restauration

## Sauvegardes automatiques

- Copie quotidienne de la base SQLite dans **`/config/backups/`**.
- Rétention : **`backup_keep`** jours (défaut 7, plage 1–90) — **Réglages → Disque et sauvegardes**.
- Le cache d’affiches TMDB sous `/config/cache/images` **n’est pas** inclus dans ces sauvegardes SQLite.

## `SECRET_KEY`

Gardez la même **`SECRET_KEY`** si vous restaurez une sauvegarde ou copiez `/config` : sinon les secrets chiffrés en base (clés API, jetons) deviennent illisibles.

## Depuis l’interface

**Réglages → Disque et sauvegardes** : composant **Sauvegarde / restauration** de la base (export et import de fichier SQLite selon les actions exposées dans l’UI).

## Réinitialiser la base

**Réglages → Réinitialiser la base** : efface fiches, téléchargements et index bibliothèque après une copie de secours dans `backups/`. **Les fichiers sur `/data` ne sont pas supprimés.**

## Sauvegarde complète NAS

Pour une reprise sur autre machine :

1. Arrêter le conteneur.
2. Copier le volume **`/config`** (base, backups, aria2.secret).
3. Copier le volume **`/data`** (médias, téléchargements, corbeille sur le **même** montage).

Vérifiez `PUID`/`PGID` et chemins `MOVIES_DIR` etc. sur le nouvel hôte.
