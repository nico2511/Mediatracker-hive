# Réglages

Dans l’application : **Réglages** (`/reglages`), puis une section du menu. Chaque section a une URL directe du type `/reglages/<slug>`.

L’index est regroupé en cinq blocs (Général, Services et jetons, Téléchargement, Médiathèque, Stockage), comme dans l’interface.

Les clés listées dans les pages ci-dessous correspondent aux réglages persistés en base. Après le premier enregistrement dans l’interface, **les valeurs affichées en UI remplacent** les variables d’environnement de départ.

## Général

| Slug | Titre dans l’UI | Documentation |
| --- | --- | --- |
| `compte` | Compte | [compte.md](compte.md) |
| `application` | Application | [application.md](application.md) |
| `maj` | Mise à jour et redémarrage | [maj.md](maj.md) |

## Services et jetons

| Slug | Titre dans l’UI | Documentation |
| --- | --- | --- |
| `jetons` | Services et jetons | [jetons.md](jetons.md) |

## Téléchargement

| Slug | Titre dans l’UI | Documentation |
| --- | --- | --- |
| `sources` | Sources | [sources.md](sources.md) |
| `qualite` | Profils de qualité | [qualite.md](qualite.md) |
| `scan` | Réglages de scan | [scan.md](scan.md) |
| `alldebrid` | AllDebrid | [alldebrid.md](alldebrid.md) |
| `hive` | Hive | [hive.md](hive.md) |

Guide Hive (usage des ruches) : [../hive.md](../hive.md).

## Médiathèque

| Slug | Titre dans l’UI | Documentation |
| --- | --- | --- |
| `jellyfin` | Jellyfin | [jellyfin.md](jellyfin.md) |
| `notifications` | Notifications | [notifications.md](notifications.md) |

Guide Jellyfin (webhook, import d’historique détaillé) : [../jellyfin.md](../jellyfin.md).

## Stockage

| Slug | Titre dans l’UI | Documentation |
| --- | --- | --- |
| `disque` | Disque et sauvegardes | [disque.md](disque.md) |
| `corbeille` | Corbeille | [corbeille.md](corbeille.md) |
| `reset` | Réinitialiser la base | [reset.md](reset.md) |

## Hors menu Réglages (liés aux réglages)

- **Bibliothèque** (`/bibliotheque`) : trois onglets *À traiter*, *Nettoyage*, *Contenu* — voir [scan.md](scan.md).
- **Médias** (`/medias`) : liste des fiches, filtres et suppression en masse — voir [medias.md](../medias.md).

L’URL `/reglages/nettoyage` redirige vers `/reglages/corbeille`.