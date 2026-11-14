<script setup lang="ts">
import { computed } from "vue";

import type { TmdbIdChangePreview, TmdbIdEpisodeLine } from "../tmdbIdChange";
import { formatEpisodeSlot } from "../tmdbIdChange";

const props = defineProps<{
  open: boolean;
  preview: TmdbIdChangePreview | null;
  busy?: boolean;
}>();

const removedSlots = computed(() => {
  const set = new Set<string>();
  for (const row of props.preview?.removed_if_rebuild ?? []) {
    if (row.season != null && row.episode != null) {
      set.add(`${row.season}:${row.episode}`);
    }
  }
  return set;
});

function sameNumberOtherShow(row: TmdbIdEpisodeLine): boolean {
  if (row.season == null || row.episode == null) {
    return false;
  }
  return !removedSlots.value.has(`${row.season}:${row.episode}`);
}

const emit = defineEmits<{
  cancel: [];
  confirm: [mode: "keep_episodes" | "rebuild_episodes"];
}>();
</script>

<template>
  <Teleport to="body">
    <div
      v-if="open && preview"
      class="dialog-backdrop"
      role="presentation"
      @click.self="!busy && emit('cancel')"
    >
      <div
        class="dialog panel"
        role="dialog"
        aria-modal="true"
        aria-labelledby="tmdb-id-change-title"
        :aria-busy="busy"
      >
        <h2 id="tmdb-id-change-title">Remplacer l'identifiant TMDB</h2>
        <p class="hint">Les mauvais épisodes quitteront la fiche. Les fichiers restent sur le disque.</p>
        <p class="tmdb-id-ids">
          TMDB <code>{{ preview.previous_tmdb_id }}</code> → <code>{{ preview.new_tmdb_id }}</code>
        </p>

        <section v-if="preview.removed_if_rebuild.length" class="tmdb-id-sec">
          <h3 class="tmdb-id-sec-title">Épisodes retirés de la fiche</h3>
          <ul class="tmdb-id-list">
            <li v-for="(row, idx) in preview.removed_if_rebuild" :key="`rm-${idx}`">
              {{ formatEpisodeSlot(row) }}
            </li>
          </ul>
        </section>

        <section v-if="preview.files_to_reassociate.length" class="tmdb-id-sec">
          <h3 class="tmdb-id-sec-title">Fichiers conservés, à réassocier</h3>
          <ul class="tmdb-id-list">
            <li v-for="(row, idx) in preview.files_to_reassociate" :key="`f-${idx}`">
              <span>{{ formatEpisodeSlot(row) }}</span>
              <template v-if="row.file_paths?.length">
                <span v-for="(path, pidx) in row.file_paths" :key="pidx" class="tmdb-id-file">{{ path }}</span>
              </template>
              <small v-if="sameNumberOtherShow(row)" class="hint">(même numéro, autre série)</small>
            </li>
          </ul>
        </section>

        <div class="tmdb-id-actions">
          <button type="button" class="primary" :disabled="busy" @click="emit('confirm', 'rebuild_episodes')">
            Reconstruire depuis TMDB
          </button>
          <button type="button" :disabled="busy" @click="emit('confirm', 'keep_episodes')">Conserver les épisodes</button>
          <button type="button" :disabled="busy" @click="emit('cancel')">Annuler</button>
        </div>
      </div>
    </div>
  </Teleport>
</template>

<style scoped>
.tmdb-id-ids code {
  background: var(--card-2);
  border-radius: var(--radius-sm);
  padding: 1px 6px;
}
.tmdb-id-sec {
  margin-top: var(--space-4);
  padding-top: var(--space-3);
  border-top: 1px solid var(--line);
}
.tmdb-id-sec-title {
  font-size: 0.8rem;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  color: var(--muted);
  margin: 0 0 var(--space-2);
  font-weight: 600;
}
.tmdb-id-list {
  margin: var(--space-2) 0 0;
  padding-left: 1.1rem;
}
.tmdb-id-list li {
  margin: 2px 0;
}
.tmdb-id-file {
  display: block;
  color: var(--warn);
  word-break: break-all;
  font-size: 0.9rem;
}
.tmdb-id-actions {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  margin-top: var(--space-5);
}
@media (min-width: 700px) {
  .tmdb-id-actions {
    flex-direction: row;
    flex-wrap: wrap;
    justify-content: flex-end;
  }
}
</style>
