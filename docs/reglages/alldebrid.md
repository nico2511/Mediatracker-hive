# AllDebrid

URL : `/reglages/alldebrid`

La **clé API** ou la liaison **PIN** se configurent sur **Réglages → Services et jetons**, carte AllDebrid.

Tant qu’AllDebrid n’est pas connecté, cette page n’affiche que le renvoi vers la carte.

## Réglages (compte connecté)

- **`alldebrid_auto_purge`** : après un rangement réussi, retire le lien enregistré, l’entrée d’historique correspondante et l’aimant du téléchargement sur le compte (activé par défaut).
- **Purger AllDebrid** : ne retire que les liens des téléchargements terminés par MediaTracker.
- **Tout purger** : confirmation requise — vide l’historique récent du compte, y compris les aimants que MediaTracker n’a pas créés.

La clé peut être préfixée via env `ALLDEBRID_API_KEY` au premier démarrage.

Voir aussi le bloc **AllDebrid** dans [Corbeille](corbeille.md) (lien rapide vers cette page).