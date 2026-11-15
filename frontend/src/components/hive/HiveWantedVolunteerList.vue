<script setup lang="ts">
import { ref } from "vue";

import { api, userFacingApiError } from "../../api";
import type { HiveWantedRequestItem } from "../../hive/hiveRequests";
import { hiveWantedStatusLine, hiveWantedTakeLabel } from "../../hive/hiveRequests";
import { mediaKind } from "../../labels";

const props = defineProps<{
  rows: HiveWantedRequestItem[];
}>();

const emit = defineEmits<{
  changed: [];
  error: [message: string];
  note: [message: string];
}>();

const busyKey = ref<string | null>(null);

function metaLine(row: HiveWantedRequestItem): string {
  const kind = mediaKind(row.type);
  if (row.season != null && row.episode != null) {
    return `${kind} · saison`;
  }
  return kind;
}

function yearSuffix(year: number | null | undefined): string {
  if (year == null) {
    return "";
  }
  return ` (${year})`;
}

async function toggleVolunteer(row: HiveWantedRequestItem) {
  busyKey.value = row.work_key;
  const release = row.volunteering;
  try {
    await api("/api/v1/hive/requests/wanted/take", {
      method: "POST",
      body: JSON.stringify({ work_key: row.work_key, release }),
    });
    emit("note", release ? "Vous ne vous en occupez plus." : "Suivi lancé : téléchargement habituel en cours.");
    emit("changed");
  } catch (err) {
    emit("error", userFacingApiError(err, release ? "Impossible de se désinscrire." : "Prise en charge impossible."));
  } finally {
    busyKey.value = null;
  }
}
</script>

<template>
  <ul v-if="props.rows.length" class="hive-wanted-list" data-testid="hive-wanted-list">
    <li v-for="row in props.rows" :key="row.work_key" class="hive-wanted-item panel row sp">
      <div
        class="hive-wanted-poster"
        :class="{ placeholder: !row.poster_url }"
        :style="row.poster_url ? { backgroundImage: `url(${row.poster_url})` } : undefined"
        aria-hidden="true"
      />
      <div class="hive-wanted-body stack tight">
        <strong
          >{{ row.title }}{{ yearSuffix(row.year) }}</strong
        >
        <span class="hint">{{ metaLine(row) }}</span>
        <span class="hint">{{ hiveWantedStatusLine(row.requester_count) }}</span>
        <span v-if="row.volunteering" class="pill ok" data-testid="hive-wanted-self">Vous vous en occupez</span>
      </div>
      <div class="hive-wanted-actions">
        <button
          type="button"
          :class="row.volunteering ? 'btn btn-sm' : 'btn btn-sm primary'"
          :data-testid="row.volunteering ? 'hive-wanted-release' : 'hive-wanted-take'"
          :disabled="busyKey === row.work_key"
          @click="toggleVolunteer(row)"
        >
          {{ hiveWantedTakeLabel(row.volunteering) }}
        </button>
      </div>
    </li>
  </ul>
  <p v-else class="hint" data-testid="hive-wanted-empty">Aucun titre recherché pour le moment.</p>
</template>

<style scoped>
.hive-wanted-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 0.65rem;
}

.hive-wanted-item {
  align-items: stretch;
  gap: 0.75rem;
}

.hive-wanted-poster {
  width: 3.25rem;
  min-width: 3.25rem;
  min-height: 4.6rem;
  border-radius: 0.35rem;
  background-size: cover;
  background-position: center;
}

.hive-wanted-poster.placeholder {
  background: color-mix(in srgb, var(--muted) 25%, var(--card));
}

.hive-wanted-body {
  flex: 1;
  min-width: 0;
}

.hive-wanted-actions {
  display: flex;
  align-items: center;
}

@media (max-width: 720px) {
  .hive-wanted-item {
    flex-wrap: wrap;
  }

  .hive-wanted-actions {
    width: 100%;
    justify-content: stretch;
  }

  .hive-wanted-actions .btn {
    flex: 1;
  }
}
</style>