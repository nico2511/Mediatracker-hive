# AllDebrid

URL : `/reglages/alldebrid`

- Bloc **compte** : clé API, état PIN si utilisé (`alldebrid_pin_session` côté serveur).
- **`alldebrid_auto_purge`** : après rangement réussi, retire lien / historique / aimant lié au job (défaut activé).
- Boutons **Purger AllDebrid** (liens des jobs terminés) et **Tout purger** (historique récent entier, avec confirmation).

La clé peut être préfixée via env `ALLDEBRID_API_KEY` au premier boot.
