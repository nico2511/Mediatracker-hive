# Mise à jour et redémarrage

URL : `/reglages/maj`

- **Version installée** : version, image, date de build, dernière vérification.
- **Vérifier la mise à jour** : comparaison registre / GitHub (jetons sur [Services et jetons](jetons.md), carte GitHub).
- **Mettre à jour maintenant** : délègue à Watchtower si URL et jeton Watchtower sont configurés (même carte **Services et jetons**).
- **Redémarrer** : redémarrage applicatif.
- **Me notifier quand Watchtower installe une nouvelle version** (`notify_on_update`) : notification via les canaux définis dans [Notifications](notifications.md) — interrupteur masqué si Watchtower n’est pas configuré.

Voir [Mises à jour](../mises-a-jour.md).