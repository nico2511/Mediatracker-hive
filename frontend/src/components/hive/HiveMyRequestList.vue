<script setup lang="ts">
import { computed, ref } from "vue";
import { RouterLink } from "vue-router";

import { api, userFacingApiError } from "../../api";
import { formatBytes } from "../../diskFormat";
import type { HiveMyRequestItem } from "../../hive/hiveRequests";
import {
  hiveRequestKindLine,
  hiveRequestMineBadge,
  hiveRequestMineSubBadge,
  hiveRequestTitleLine,
} from "../../hive/hiveRequests";

export interface DownloadProgressRow {
  media_item_id: number | null;
  progress: number;
  done_bytes: number;
  size: number | null;
  state: string;
}

const props = defineProps<{
  items: HiveMyRequestItem[];
  downloads?: DownloadProgressRow[];
}>();

const emit = defineEmits<{ changed: [] }>();

const busyItem = ref<number | null>(null);
const error = ref("");

const downloadByItem = computed(() => {
  const map = new Map<number, DownloadProgressRow>();
  for (const job of props.downloads ?? []) {
    if (job.media_item_id != null && !map.has(job.media_item_id)) {
      map.set(job.media_item_id, job);
    }
  }
  return map;
});

function transferLine(item: HiveMyRequestItem): string {
  const job = downloadByItem.value.get(item.media_item_id);
  if (!job || item.status !== "transfer") {
    return "même file que les téléchargements";
  }
  const pct = Math.round(job.progress);
  const done = formatBytes(job.done_bytes);
  const total = job.size ? formatBytes(job.size) : "—";
  return `${pct} % · ${done} sur ${total} · même file que les téléchargements`;
}

async function announce(item: HiveMyRequestItem) {
  busyItem.value = item.media_item_id;
  error.value = "";
  try {
    await api("/api/v1/hive/requests/announce", {
      method: "POST",
      body: JSON.stringify({ media_item_id: item.media_item_id }),
    });
    emit("changed");
  } catch (err) {
    error.value = userFacingApiError(err, "Annonce impossible.");
  } finally {
    busyItem.value = null;
  }
}

async function retract(item: HiveMyRequestItem) {
  busyItem.value = item.media_item_id;
  error.value = "";
  try {
    await api("/api/v1/hive/requests/retract", {
      method: "POST",
      body: JSON.stringify({ media_item_id: item.media_item_id }),
    });
    emit("changed");
  } catch (err) {
    error.value = userFacingApiError(err, "Retrait impossible.");
  } finally {
    busyItem.value = null;
  }
}
</script>

<template>
  <div class="stack">
    <p v-if="error" class="error" role="alert">{{ error }}</p>
    <ul v-if="items.length" class="hive-mine-list" data-testid="hive-mine-list">
      <li v-for="item in items" :key="item.media_item_id" class="hive-mine-item panel">
        <div class="hive-mine-body stack tight">
          <RouterLink class="hive-mine-title" :to="`/medias/${item.media_id}`">{{ hiveRequestTitleLine(item) }}</RouterLink>
          <span class="hint">{{ hiveRequestKindLine(item) }}</span>
          <p v-if="hiveRequestMineBadge(item.status)" class="hive-mine-badges">
            <span class="pill honey">{{ hiveRequestMineBadge(item.status) }}</span>
            <span class="pill outline">{{ hiveRequestMineSubBadge(item.status) }}</span>
          </p>
          <p v-else class="hint">Recherche habituelle sans résultat — annonce possible.</p>
          <div
            v-if="item.status === 'transfer'"
            class="hive-mine-progress"
            role="progressbar"
            :aria-valuenow="downloadByItem.get(item.media_item_id)?.progress ?? 0"
            aria-valuemin="0"
            aria-valuemax="100"
          >
            <span class="pill ok">En cours de transfert</span>
            <div class="hive-mine-bar" aria-hidden="true">
              <i :style="{ width: `${downloadByItem.get(item.media_item_id)?.progress ?? 12}%` }" />
            </div>
            <span class="hint">{{ transferLine(item) }}</span>
          </div>
        </div>
        <div class="hive-mine-actions">
          <button
            v-if="(item.status === 'prompt' || item.status === 'retracted') && item.hive_request_available"
            type="button"
            class="btn primary"
            data-testid="hive-mine-announce"
            :disabled="busyItem === item.media_item_id"
            @click="announce(item)"
          >
            Demander à la ruche
          </button>
          <button
            v-else-if="item.status !== 'prompt' && item.status !== 'retracted'"
            type="button"
            class="btn"
            data-testid="hive-mine-retract"
            :disabled="busyItem === item.media_item_id"
            @click="retract(item)"
          >
            Ne plus demander à la ruche
          </button>
        </div>
      </li>
    </ul>
    <article v-else class="panel hive-mine-empty" data-testid="hive-mine-empty">
      <p class="hive-mine-empty-title"><strong>Aucun suivi en attente envoyé à la ruche</strong></p>
      <p class="hint">
        Quand un média suivi ne se télécharge pas, il peut être annoncé anonymement à la ruche, selon le réglage
        « Demander à la ruche quand rien n’est trouvé ». Rien n’apparaît ici tant que cette annonce n’a pas été faite.
      </p>
      <RouterLink class="btn" to="/reglages/hive">Ouvrir les réglages Hive</RouterLink>
    </article>
  </div>
</template>

<style scoped>
.hive-mine-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: grid;
  gap: 0.65rem;
}

.hive-mine-item {
  display: flex;
  flex-wrap: wrap;
  align-items: flex-start;
  justify-content: space-between;
  gap: 0.75rem;
  padding: 0.75rem 0.85rem;
}

.hive-mine-body {
  flex: 1 1 14rem;
  min-width: 0;
}

.hive-mine-title {
  color: inherit;
  text-decoration: none;
  font-weight: 700;
}

.hive-mine-title:hover {
  text-decoration: underline;
}

.hive-mine-badges {
  display: flex;
  flex-wrap: wrap;
  gap: 0.35rem;
  margin: 0.25rem 0 0;
}

.pill.honey {
  background: color-mix(in srgb, var(--honey, #d9b46a) 22%, transparent);
  border: 1px solid color-mix(in srgb, var(--honey, #d9b46a) 40%, transparent);
}

.pill.outline {
  background: transparent;
  border: 1px solid var(--hair);
  color: var(--muted);
}

.hive-mine-progress {
  display: grid;
  gap: 0.35rem;
  margin-top: 0.35rem;
}

.hive-mine-bar {
  height: 0.35rem;
  background: rgb(255 255 255 / 10%);
  border-radius: 2px;
  overflow: hidden;
}

.hive-mine-bar > i {
  display: block;
  height: 100%;
  background: var(--ok);
}

.hive-mine-actions {
  display: flex;
  flex-direction: column;
  gap: 0.45rem;
  min-width: min(100%, 14rem);
}

.hive-mine-empty {
  padding: 1.25rem 1rem;
  text-align: center;
  display: grid;
  gap: 0.75rem;
  justify-items: center;
}

.hive-mine-empty-title {
  margin: 0;
}

.stack.tight {
  gap: 0.2rem;
}
</style>