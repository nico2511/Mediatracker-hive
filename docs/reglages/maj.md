# Mise à jour et redémarrage

URL : `/reglages/maj`

- **Version installée** : version, image, date de build, dernière vérification.
- **Vérifier la mise à jour** : comparaison registre / GitHub (jetons sur [API et jetons](jetons.md), carte GitHub).
- **Mettre à jour maintenant** : délègue à Watchtower si URL et jeton Watchtower sont configurés (même carte **API et jetons**).
- **Redémarrer** : redémarrage applicatif.
- **Me notifier quand une nouvelle version démarre** (`notify_on_update`) : notification via les canaux définis dans [Notifications](notifications.md) lorsque l’application redémarre avec une image plus récente.

Voir [Mises à jour](../mises-a-jour.md).