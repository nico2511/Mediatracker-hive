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
- **Membres** (`/hive/ruches/<id>/membres`) : validation des demandes (créateur de la ruche), quotas, exclusion.

Le mode **tirage seul** (`pull_only`) : l’instance peut recevoir des fichiers sans en envoyer (affiché dans l’interface si applicable).

## Catalogue et partage

- **Catalogue Hive** : œuvres partagées par les pairs ; bouton **Suivre** côté récepteur.
- **Repartage automatique** : option **Repartager automatiquement les fichiers reçus** (`hive_catalog_auto_share`, **désactivée** par défaut).
- **Accueil** : section **Dernières dispos Hive** lorsque des entrées existent.

Avant qu’un fichier soit visible des pairs, l’instance calcule une **empreinte** (empreinte SHA par blocs du fichier) via un processus dédié dans le conteneur.

## Téléchargements

Hive est une **source** comme les autres dans l’ordre de téléchargement (`hive` dans `download_source_order`). Lorsqu’un fichier est disponible chez un pair :

1. MediaTracker obtient un **grant** de lecture temporaire, signé par le pair.
2. **aria2** (téléchargeur dans le conteneur) récupère le fichier en HTTP avec l’en-tête Hive prévu.
3. Le fichier apparaît dans **Téléchargements** puis est rangé comme un téléchargement AllDebrid classique.

Il n’y a **pas** d’écran de transferts Hive séparé : tout passe par la file **Téléchargements** habituelle.

Si le pair est injoignable, l’orchestrateur peut **essayer la source suivante** selon l’ordre configuré dans **Réglages → Sources**.

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
