# Hive

URL : `/reglages/hive`

Page composant dédié (pas dans `SettingsSectionView`).

Réglages persistés : `hive_enabled`, `hive_public_address`, `hive_instance_name` (via activation), `hive_catalog_auto_share`, `hive_only`, `hive_request_on_miss`, `hive_transfer_parallel`, `hive_transfer_send_bps`, `hive_transfer_recv_bps`, `hive_transfer_window_start`, `hive_transfer_window_end`.

**Ruche seule** (`hive_only`, défaut désactivé) : lorsque c’est activé, seule la source Hive est utilisable pour le téléchargement automatique, la recherche planifiée et le fil d’actualité des sources — même si une clé AllDebrid est configurée. Les tests manuels dans Réglages → Sources restent possibles. L’API refuse d’activer ce réglage si Hive est coupé (erreur 422) ; si Hive est désactivé ensuite, le réglage est sans effet. Le signal **cherche** après un miss suit toujours `hive_request_on_miss`.

Guide utilisateur : [Hive](../hive.md).

L’écran **Membres** d’une ruche affiche, sous chaque nom, la version MediaTracker apprise par gossip (`instance` sur `/hive/ruche/{id}/gossip/sync`). Aucun réglage supplémentaire : la valeur locale est annoncée à chaque synchronisation pair.
