# AllDebrid

URL : `/reglages/alldebrid`

La **clé API** ou la liaison **PIN** se configurent sur **Réglages → API et jetons**, carte AllDebrid.

Tant qu’AllDebrid n’est pas connecté, cette page n’affiche que le renvoi vers la carte.

## Réglages (compte connecté)

- **`alldebrid_auto_purge`** : après un rangement réussi, retire le lien enregistré, l’entrée d’historique correspondante et l’aimant du téléchargement sur le compte (activé par défaut).
- **`alldebrid_magnet_wait_max_seconds`** (défaut **600**) : durée maximale pendant laquelle le worker laisse un aimant déjà uploadé sur le compte AllDebrid se préparer avant d’abandonner l’attente et de poursuivre la cascade source (repli hébergeur, autre source, etc.). L’aimant expiré n’est pas marqué mort : il est seulement ignoré pour le job en cours. **Pas de champ dans l’interface** : clé dans la table **`setting`**, modifiable par `PATCH /api/v1/settings` avec `{"values": {"alldebrid_magnet_wait_max_seconds": 900}}` (session admin + CSRF). Si la ligne absente, le worker utilise la variable d’environnement **`ALLDEBRID_MAGNET_WAIT_MAX_SECONDS`** (sinon **600**). Bornes validées : **30** à **86 400** secondes.
- **`alldebrid_magnet_poll_seconds`** (défaut **30**) : intervalle minimal entre deux sondages `magnet_status` pour un job en attente (`ALLDEBRID_WAIT`). Règle `next_retry_at` du téléchargement ; ne bloque pas le passage worker. **Pas d’interface** ; même mécanisme `PATCH /api/v1/settings` ou table **`setting`**. Repli sur **`ALLDEBRID_MAGNET_POLL_SECONDS`** si la clé n’est pas en base (sinon **30**). Bornes : **5** à **600** secondes (le moteur impose au minimum 5 s).
- **Purger AllDebrid** : ne retire que les liens des téléchargements terminés par MediaTracker.
- **Tout purger** : confirmation requise — vide l’historique récent du compte, y compris les aimants que MediaTracker n’a pas créés.

La clé API peut être préfixée via env `ALLDEBRID_API_KEY` au premier démarrage.

Voir aussi le bloc **AllDebrid** dans [Corbeille](corbeille.md) (lien rapide vers cette page).