# Jellyfin

URL : `/reglages/jellyfin`

**URL** et **clé API** se renseignent sur **Réglages → Services et jetons**, carte Jellyfin. Tant que Jellyfin n’est pas « connecté » (URL + clé enregistrées), cette page n’affiche que le renvoi vers la carte.

Guide détaillé (webhook, chemins Docker, dépannage) : [../jellyfin.md](../jellyfin.md).

## Contenu de la page (Jellyfin connecté)

- **Utilisateur Jellyfin** : compte dont l’état « vu » est synchronisé avec MediaTracker (liste chargée depuis le serveur).
- **Correspondance des chemins** (`jellyfin_path_map`) : paires `préfixe_côté_Jellyfin=chemin_MediaTracker`, séparées par `;`.
- **Ancien préfixe** (`jellyfin_path_prefix`) : repli si la correspondance ne couvre pas un chemin.
- **Renommage automatique** (`jellyfin_auto_rename`) : désactivé par défaut.
- **Pousser un vu manuel** (`jellyfin_push_watched`) : désactivé par défaut ; un vu posé à la main n’est pas effacé par la synchro entrante.
- **Synchro « vu » (minutes)** (`jellyfin_poll_interval_min`) : passage périodique ; le **webhook** met à jour plus vite s’il est configuré.
- **Webhook** : URL et jeton affichés pour le plugin Jellyfin (en-tête `X-MediaTracker-Token` prioritaire sur `?token=`).
- Boutons **Tester la connexion** et **Synchroniser les vus**.

Après chaque fichier déposé, MediaTracker demande aussi un rafraîchissement des bibliothèques Jellyfin (environ une minute).

## Import d’historique « vu »

L’import hors ligne (copie de base Jellyfin ou CSV) ne figure plus dans le menu principal **Bibliothèque**. Il est regroupé avec la configuration Jellyfin :

1. Renseigner URL et clé sur **Services et jetons**, carte Jellyfin.
2. Sur la même carte, **Importer l’historique** ouvre l’écran d’import (route dédiée sous `/bibliotheque/…`).

Deux modes sur cet écran : **Bases Jellyfin** (recommandé) et **Fichier CSV**. Le suivi reste désactivé tant que vous ne le réactivez pas après l’import.

Étapes, formats et limites : section *Importer l’historique « vu » depuis Jellyfin* dans [../jellyfin.md](../jellyfin.md).

## Bibliothèque

Avec Jellyfin connecté, l’onglet **À traiter** de la **Bibliothèque** peut proposer l’**alignement Jellyfin** (fusion ou rattachement de fiches selon l’état vu côté serveur). Voir [scan.md](scan.md).