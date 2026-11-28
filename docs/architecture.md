# Carte d'architecture MediaTracker

Document de repérage rapide pour le dépôt (instance d'exemple : `https://mediatracker.example.com`). Détail opérationnel : [interne/REFERENCE-DEV.md](interne/REFERENCE-DEV.md).

## Par domaine

| Domaine | Fichiers / dossiers | Point d'entrée (une ligne) |
| --- | --- | --- |
| **Worker / planification** | `backend/mediatracker/worker.py`, `backend/mediatracker/core/worker_schedule.py`, `backend/mediatracker/core/worker_wake.py` | Boucle unique verrouillée par `config_dir/worker.lock` : passe `_worker_pass_body`, puis sommeil jusqu'à la prochaine échéance (`compute_sleep_seconds`, filet 60 s). |
| **Hive (pairs, catalogue, empreintes)** | `backend/mediatracker/hive/`, `backend/mediatracker/api/hive_router.py`, `backend/mediatracker/api/v1/hive.py`, `backend/mediatracker/hive/fingerprint_daemon.py` | API UI sous `/api/v1/hive/*` ; protocole pair monté par `mount_hive_router` ; empreintes SHA hors worker principal (`fingerprint_daemon.py`, s6). |
| **Jellyfin** | `backend/mediatracker/integrations/jellyfin_sync.py`, `backend/mediatracker/integrations/jellyfin.py`, `backend/mediatracker/library/jellyfin_align.py`, `backend/mediatracker/library/jellyfin_push.py` | Refresh bibliothèque différé (60 s) après dépôt ; synchro « vu » par webhook ou polling (`maybe_sync_jellyfin`). |
| **AllDebrid / aria2** | `backend/mediatracker/providers/debrid/alldebrid.py`, `backend/mediatracker/providers/downloaders/aria2.py`, `backend/mediatracker/core/pipeline/`, `backend/mediatracker/core/aria2_events.py` | `tick()` démarre les jobs ; déblocage réseau hors SAVEPOINT ; suivi aria2 2–10 s si téléchargements actifs. |
| **Sources de téléchargement** | `backend/mediatracker/providers/sources/`, `backend/mediatracker/core/source_poll.py`, `backend/mediatracker/core/source_poll_tick.py` | Un média suivi dû par passe worker ; registre plugins dans `backend/mediatracker/providers/sources/__init__.py`. |
| **Bibliothèque / rangement / parse** | `backend/mediatracker/library/scanner.py`, `backend/mediatracker/library/parse.py`, `backend/mediatracker/library/regroup.py`, `backend/mediatracker/core/naming.py` | Scan disque périodique ou forcé ; parse des noms de release ; déplacement atomique vers `Films` / `Series` après job. |
| **TMDB** | `backend/mediatracker/integrations/tmdb.py`, `backend/mediatracker/integrations/tmdb_enrichment.py`, `backend/mediatracker/integrations/tmdb_images.py` | Client lecture API ; enrichissement fiches worker ; cache affiches locale (`/config/cache/images`). |
| **Home Assistant** | `backend/mediatracker/api/v1/ha.py`, `docs/home-assistant.md` | `GET /api/v1/ha/summary` (lecture seule) ; boutons réutilisent scan, catalogue et file de téléchargement. |
| **Réglages** | `backend/mediatracker/settings_store.py`, `backend/mediatracker/api/v1/settings.py`, `frontend/src/views/settings/` | Clés validées en PATCH `/api/v1/settings` ; écrans `/reglages/<section>` (`frontend/src/router.ts`). |
| **API v1 / processus HTTP** | `backend/mediatracker/api/app.py`, `backend/mediatracker/api/serve.py`, `backend/mediatracker/api/v1/router.py` | Uvicorn sur `mediatracker.api.app:app` ; routes REST sous `/api/v1`. |
| **Sécurité** | `backend/mediatracker/security/`, CSRF et sessions dans `backend/mediatracker/api/app.py` | Jetons session, CSRF, rate-limit login, en-têtes, chiffrement secrets, garde SSRF sortant. |
| **Métro / collecte de passages** | `backend/mediatracker/metro/record.py`, `backend/mediatracker/metro/ui_graph.py`, `backend/mediatracker/metro/summary.py`, `backend/mediatracker/api/v1/task_runs.py`, `backend/mediatracker/api/v1/metro.py`, `frontend/src/views/HealthActivityView.vue` | Instrumentation worker → table `task_run` ; graphe statique et agrégats 24 h via `GET /api/v1/metro/*` ; carte lecture seule onglet Santé › Activité. |
| **Frontend Vue** | `frontend/src/main.ts`, `frontend/src/router.ts`, `frontend/src/views/`, `frontend/src/composables/` | SPA Vue 3 + Vite ; routes enfants sous `AppLayout` ; SSE progrès via `useProgressStream`. |

## Tâches périodiques (worker) et appels externes

Le worker enchaîne les lignes ci-dessous à **chaque passe** ; une ligne ne fait du travail réseau que si son échéance est atteinte (sinon no-op rapide). Fréquence « réglage » = clé SQLite ou variable d'environnement Pydantic ; défaut issu de `backend/mediatracker/config.py` sauf mention.

| Tâche | Fichier | Déclencheur | Fréquence (réglage → défaut) | Service externe |
| --- | --- | --- | --- | --- |
| Boucle worker | `backend/mediatracker/worker.py` | Verrou + timer / réveil fichier | Actif : 2 s (aria2) ou 10 s (WS aria2) ; inactif : prochaine échéance, max 60 s | — |
| Pipeline téléchargements | `backend/mediatracker/core/pipeline/` via `tick()` | Chaque passe | Continu si jobs `pending` / actifs | AllDebrid, aria2 RPC, pairs Hive |
| Réconciliation aria2 | `backend/mediatracker/core/aria2_reconcile.py` | Chaque passe | Si écarts détectés | aria2 |
| Scan bibliothèque | `backend/mediatracker/library/scanner.py` | `maybe_scan_library` | `library_scan_interval_hours` → **24 h** ; aussi inotify / post-dépôt | Disque local |
| Synchro Jellyfin | `backend/mediatracker/integrations/jellyfin_sync.py` | `REFRESH_AT` / `LAST_POLL_AT` | Refresh **60 s** après dépôt ; poll `jellyfin_poll_interval_min` → **30 min** | Jellyfin HTTP |
| Alignement / push Jellyfin | `backend/mediatracker/library/jellyfin_align.py`, `backend/mediatracker/library/jellyfin_push.py` | Drapeaux (`align_pending`, `restore_pending`, `identity_due`) ou cache bibliothèque échu (`jellyfin_poll_interval_min`) ; push : job en file | Après refresh ou action utilisateur ; no-op sans travail dû | Jellyfin |
| Sondage sources | `backend/mediatracker/core/source_poll_tick.py` | `Media.next_source_poll_at` | `scan_interval_min` → **720 min** (par média, surcharge possible) | Wawacity, Comet, Torrentio, addons Stremio, etc. |
| Catalogue nouveautés | `backend/mediatracker/core/catalog.py` | `catalog.next_refresh_at` | `catalog_interval_hours` → **12 h** | Sources catalogue + TMDB (matching) |
| Sorties numériques | `backend/mediatracker/core/digital_releases.py` | Horodatage interne | **12 h** (fixe code) | TMDB |
| Saisons / films / enrichissement | `backend/mediatracker/core/seasons.py` | Une fiche par passe | Série/film suivi : **1/j** ; non suivi : **1/semaine** | TMDB |
| Liens morts AllDebrid | `backend/mediatracker/core/dead_links.py` | `RECHECK_KEY` | **7 j** (fixe code) | AllDebrid |
| Maintenance corbeille / rétention | `backend/mediatracker/core/cleanup.py` | `should_run_classify` | Classification : **60 s** mini entre passes | Disque local |
| Gossip Hive | `backend/mediatracker/hive/gossip_worker.py` | `hive_gossip_next_at` | ~**600 ± 120 s** (fixe code) | Pairs Hive HTTPS |
| Sync catalogue Hive (RUC2) | `backend/mediatracker/hive/catalog.py` | Génération / intervalle | `hive_catalog_full_sync_interval_sec` → **6 h** | Disque local (+ pairs si partage) |
| Signaux « cherche » Hive | `backend/mediatracker/hive/signal_policy.py` | Catch-up worker | Après activation / rattrapage | — |
| Notifications webhooks | `backend/mediatracker/events/deliver.py` | Chaque passe + file `WebhookDelivery` | Retries **1 min → 6 h** (fixe code) | Home Assistant, ntfy, webhooks HTTP |
| Sauvegarde SQLite | `backend/mediatracker/maintenance/backup.py` | Calendrier journalier | `backup_keep` → **7** fichiers | Disque `/config/backups` |
| Purge cache images TMDB | `backend/mediatracker/integrations/tmdb_images.py` | `image_cache_purge_at` | **1/j** (fixe code) | — (fichiers locaux) |
| Purge journal / états sources | `backend/mediatracker/events/retention.py`, `backend/mediatracker/core/source_state.py` | Tampon **1/j** | Rétention **30 j** (`backend/mediatracker/retention.py`) | — |
| Téléchargements incomplets | `backend/mediatracker/core/incomplete.py` | Hors SAVEPOINT, **1/h** max | `incomplete_cleanup_enabled` → **désactivé** | Disque local |
| Historique métriques | `backend/mediatracker/core/metrics/history.py` | `metrics.history_last_at` | **5 min** (fixe code) | — |
| SQLite VACUUM/ANALYZE | `backend/mediatracker/maintenance/sqlite_tune.py` | Fenêtre locale | `sqlite_maintenance_hour` → **4 h** (TZ notifs) | Disque local |
| Purge passages métro | `backend/mediatracker/metro/record.py` | Monotonique worker | Intervalle mini interne ; rétention **7 j** | — |
| Empreintes Hive (daemon) | `backend/mediatracker/hive/fingerprint_daemon.py` | File SQLite + réveil | Continu (un fichier à la fois) | Disque local |

### Rafraîchissements périodiques frontend (appels backend)

| Écran / composant | Fichier | Déclencheur | Période | Appels API |
| --- | --- | --- | --- | --- |
| Santé | `frontend/src/views/HealthView.vue` | `setInterval` | **20 s** | `/api/v1/health` (métriques) |
| Coque Hive | `frontend/src/views/hive/HiveShell.vue` | Tick **1 s**, recharge si gossip dû | Aligné sur `next_gossip_at` ; retry **60 s** | `/api/v1/hive/overview`, compteurs demandes |
| Empreintes Hive | `frontend/src/components/HiveEmpreintesPanel.vue` | Panneau ouvert | **5 s** (`PANEL_POLL_INTERVAL_MS`) | `/api/v1/hive/media/{id}/empreintes` |
| Partage Hive (fiche) | `frontend/src/composables/useHiveSharePolling.ts` | Session partage active | **5 s**, max **120 s** / 24 essais | `/api/v1/hive/items/{id}/sharing` |
| Compte AllDebrid | `frontend/src/components/AllDebridAccountBlock.vue` | Phase PIN | **5 s** + décompte **1 s** | `/api/v1/integrations/alldebrid/account/pin/poll` |
| Vérification bibliothèque | `frontend/src/composables/useLibraryVerifyAll.ts` | Job en cours | **3 s** | `/api/v1/library/verify-all` |
| Redémarrage (MAJ) | `frontend/src/views/settings/SettingsSectionView.vue` | Après « Mettre à jour » | **3 s** jusqu'à 180 s | `/api/v1/health` |
| Progrès téléchargements | `frontend/src/composables/useProgressStream.ts` | SSE (pas intervalle) | Connexion longue | `/api/v1/progress-stream` |

## Mettre à jour cette carte

Toute modification qui ajoute, retire ou déplace un module métier, une tâche périodique worker ou un polling UI visible doit mettre à jour la ligne correspondante dans ce fichier (domaine **ou** tableau) dans la même pull request.

Vérification des chemins cités : `python3 scripts/verify_architecture_paths.py` (sortie attendue : tous les fichiers existent).