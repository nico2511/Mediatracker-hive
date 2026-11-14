# Mise à jour et redémarrage

URL : `/reglages/maj`

- Affiche version, SHA image, date de build, dernière vérification GHCR/GitHub.
- **Vérifier la mise à jour** : compare au registre (jetons dans [Jetons](jetons.md)).
- **Mettre à jour maintenant** : délègue à Watchtower si `watchtower_url` + `watchtower_token` sont configurés.
- **Redémarrer** : redémarrage applicatif.
- **`notify_on_update`** : notification après mise à jour automatique Watchtower.

Voir [Mises à jour](../mises-a-jour.md).
