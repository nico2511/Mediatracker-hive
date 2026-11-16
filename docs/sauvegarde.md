# Sauvegarde et restauration

## Sauvegardes automatiques

- Copie quotidienne de la base SQLite dans **`/config/backups/`**.
- Rétention : **`backup_keep`** jours (défaut 7, plage 1–90) — **Réglages → Disque et sauvegardes**.
- Le cache d’affiches TMDB sous `/config/cache/images` **n’est pas** inclus dans ces sauvegardes SQLite.

## `SECRET_KEY`

Gardez la même **`SECRET_KEY`** si vous restaurez une sauvegarde ou copiez `/config` : sinon les secrets chiffrés en base (clés API, jetons) deviennent illisibles.

## Depuis l’interface

**Réglages → Disque et sauvegardes** : composant **Sauvegarde / restauration** de la base.

- **Liste** des copies automatiques et des copies de sécurité (avant réinitialisation ou restauration).
- **Télécharger** : enregistre le fichier `.db` sur votre appareil (sauvegardes quotidiennes ou copies de sécurité).
- **Importer** : envoi d’un fichier `.db` ; l’application l’enregistre dans `backups/` puis ouvre le même flux qu’une restauration (aperçu : version, schéma, nombre de fiches, compatibilité).
- **Restaurer** : confirmation explicite ; une copie de sécurité de la base courante est créée avant remplacement. Après succès, rechargement de l’application.

Les sauvegardes dont le nom commence par `import-` proviennent d’un import manuel. Gardez la **`SECRET_KEY`** de l’instance d’origine si la base contient des secrets chiffrés ; l’identité **Hive** est conservée telle quelle dans le fichier importé.

## Réinitialiser la base

**Réglages → Réinitialiser la base** : efface fiches, téléchargements et index bibliothèque après une copie de secours dans `backups/`. **Les fichiers sur `/data` ne sont pas supprimés.**

## Sauvegarde complète NAS

Pour une reprise sur autre machine :

1. Arrêter le conteneur.
2. Copier le volume **`/config`** (base, backups, aria2.secret).
3. Copier le volume **`/data`** (médias, téléchargements, corbeille sur le **même** montage).

Vérifiez `PUID`/`PGID` et chemins `MOVIES_DIR` etc. sur le nouvel hôte.