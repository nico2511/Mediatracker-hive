# Dette `vue-tsc` (référence main)

`npm run typecheck` exécute `vue-tsc --noEmit --skipLibCheck` et doit échouer si de nouvelles erreurs apparaissent hors de cette liste (fichiers et thèmes connus sur `main`) :

- `src/components/DatabaseBackupRestore.vue` — `RequestInit.json`, attribut booléen nullable
- `src/components/OfflineFollowPanel.vue` — `SyncResultRow` vs `SyncOutcome`
- `src/composables/useProgressStream.ts` — comparaison number/string
- `src/sw.ts` — types Service Worker (`ExtendableEvent`)
- `src/views/LibraryView.vue` — clé `:key` nullable, `manual` nullable
- `src/views/MediaDetailView.vue` — cast `MediaRow`, type de ref route
- `src/views/SetupView.vue` — valeurs de formulaire `string | number | true`

Les changements de la PR affiches ne doivent pas ajouter d’entrées à cette liste.
