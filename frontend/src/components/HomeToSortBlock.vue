<script setup lang="ts">
import { computed, ref } from "vue";
import { RouterLink } from "vue-router";

import { api, userFacingApiError } from "../api";

export interface ToSortItem {
  media_id: number;
  media_title: string;
  poster_url?: string | null;
  kind: "renewed" | "unwatched_season" | "stop_follow";
  detail: string;
  highlight: string | null;
  season: number | null;
  seasons: number[];
  ignore_key: string;
}

export interface ToSortPayload {
  total: number;
  footer: string;
  groups: {
    kind: ToSortItem["kind"];
    title: string;
    items: ToSortItem[];
  }[];
}

const props = defineProps<{
  board: ToSortPayload | null;
  error: string;
  loading: boolean;
}>();

const emit = defineEmits<{
  changed: [];
}>();

const busy = ref<Record<string, boolean>>({});
const localError = ref("");

const visible = computed(
  () => !!props.error || !!localError.value || (props.board !== null && props.board.total > 0),
);

const displayError = computed(() => props.error || localError.value);

function detailParts(item: ToSortItem): { before: string; after: string } {
  if (!item.highlight) {
    return { before: item.detail, after: "" };
  }
  const marker = item.highlight;
  const idx = item.detail.indexOf(marker);
  if (idx < 0) {
    return { before: item.detail, after: "" };
  }
  return {
    before: item.detail.slice(0, idx),
    after: item.detail.slice(idx + marker.length),
  };
}

async function withBusy(key: string, work: () => Promise<void>) {
  if (busy.value[key]) {
    return;
  }
  busy.value[key] = true;
  localError.value = "";
  try {
    await work();
    emit("changed");
  } catch (err) {
    localError.value = userFacingApiError(err, "Action impossible.");
  } finally {
    busy.value[key] = false;
  }
}

async function ignoreItem(item: ToSortItem) {
  await withBusy(`ignore-${item.ignore_key}`, async () => {
    await api("/api/v1/dashboard/to-sort/ignore", {
      method: "POST",
      body: JSON.stringify({ ignore_key: item.ignore_key }),
    });
  });
}

async function followAgain(item: ToSortItem) {
  await withBusy(`follow-${item.media_id}`, async () => {
    await api(`/api/v1/media/${item.media_id}/follow`, {
      method: "POST",
      body: JSON.stringify({}),
    });
  });
}

async function unfollow(item: ToSortItem) {
  if (!window.confirm(`Ne plus suivre « ${item.media_title} » ? Les fichiers et l'historique « vu » restent.`)) {
    return;
  }
  await withBusy(`unfollow-${item.media_id}`, async () => {
    await api(`/api/v1/media/${item.media_id}/unfollow`, {
      method: "POST",
      body: JSON.stringify({ trash: false }),
    });
  });
}

function secondaryLabel(kind: ToSortItem["kind"]): string {
  return kind === "stop_follow" ? "Garder" : "Ignorer";
}

function primaryLabel(kind: ToSortItem["kind"]): string {
  if (kind === "renewed") {
    return "Suivre";
  }
  if (kind === "stop_follow") {
    return "Arrêter le suivi";
  }
  return "Voir la fiche";
}

async function primaryAction(item: ToSortItem) {
  if (item.kind === "renewed") {
    await followAgain(item);
    return;
  }
  if (item.kind === "stop_follow") {
    await unfollow(item);
  }
}

async function secondaryAction(item: ToSortItem) {
  await ignoreItem(item);
}
</script>

<template>
  <section v-if="visible" id="home-to-sort" class="home-to-sort">
    <header class="home-to-sort-header">
      <h2>À trier</h2>
      <span v-if="board && board.total > 0" class="hint home-to-sort-count"
        >{{ board.total }} signalement{{ board.total > 1 ? "s" : "" }}</span
      >
    </header>
    <p v-if="displayError" class="error">{{ displayError }}</p>
    <div v-if="board && board.total > 0">
      <template v-for="group in board.groups" :key="group.kind">
        <h3 class="home-to-sort-group">{{ group.title }}</h3>
        <ul class="home-to-sort-list">
          <li v-for="item in group.items" :key="item.ignore_key" class="home-to-sort-row">
          <RouterLink :to="`/medias/${item.media_id}`" class="home-to-sort-poster" :title="item.media_title">
            <img v-if="item.poster_url" :src="item.poster_url" alt="" loading="lazy" />
            <span v-else class="home-to-sort-poster-fallback">{{ item.media_title.slice(0, 1) }}</span>
          </RouterLink>
          <div class="home-to-sort-body">
            <strong>{{ item.media_title }}</strong>
            <p class="home-to-sort-detail">
              <template v-if="item.highlight">
                {{ detailParts(item).before }}<span class="home-to-sort-highlight">{{ item.highlight }}</span
                >{{ detailParts(item).after }}
              </template>
              <template v-else>{{ item.detail }}</template>
            </p>
          </div>
          <div class="home-to-sort-actions">
            <RouterLink
              v-if="item.kind === 'unwatched_season'"
              :to="`/medias/${item.media_id}`"
              class="button home-to-sort-primary"
            >
              {{ primaryLabel(item.kind) }}
            </RouterLink>
            <button
              v-else
              type="button"
              class="button home-to-sort-primary"
              :disabled="busy[`follow-${item.media_id}`] || busy[`unfollow-${item.media_id}`]"
              @click="primaryAction(item)"
            >
              {{ primaryLabel(item.kind) }}
            </button>
            <button
              type="button"
              class="home-to-sort-secondary"
              :disabled="busy[`ignore-${item.ignore_key}`]"
              @click="secondaryAction(item)"
            >
              {{ secondaryLabel(item.kind) }}
            </button>
          </div>
          </li>
        </ul>
      </template>
      <p class="hint home-to-sort-footer">{{ board.footer }}</p>
    </div>
  </section>
</template>

<style scoped>
.home-to-sort {
  margin-top: 1.5rem;
  padding-top: 0.5rem;
}

.home-to-sort-header {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 0.75rem;
  margin-bottom: 0.75rem;
}

.home-to-sort-header h2 {
  margin: 0;
  font-size: 1.15rem;
}

.home-to-sort-count {
  margin: 0;
  white-space: nowrap;
}

.home-to-sort-group {
  margin: 1rem 0 0.5rem;
  font-size: 0.82rem;
  font-weight: 600;
  color: var(--muted, #9ca3af);
}

.home-to-sort-list {
  list-style: none;
  margin: 0;
  padding: 0;
}

.home-to-sort-row {
  display: grid;
  grid-template-columns: auto 1fr;
  grid-template-rows: auto auto;
  gap: 0.65rem 0.75rem;
  align-items: start;
  padding: 0.75rem 0;
  border-bottom: 1px solid var(--hair, rgba(255, 255, 255, 0.08));
}

.home-to-sort-row:last-child {
  border-bottom: 0;
}

.home-to-sort-poster {
  grid-row: 1 / span 2;
  width: 3.25rem;
  aspect-ratio: 2 / 3;
  border-radius: 0.35rem;
  overflow: hidden;
  background: var(--card, #1f2937);
  display: flex;
  align-items: center;
  justify-content: center;
  text-decoration: none;
}

.home-to-sort-poster img {
  width: 100%;
  height: 100%;
  object-fit: cover;
  display: block;
}

.home-to-sort-poster-fallback {
  font-weight: 700;
  opacity: 0.55;
}

.home-to-sort-body strong {
  display: block;
  margin-bottom: 0.2rem;
}

.home-to-sort-detail {
  margin: 0;
  font-size: 0.92rem;
  line-height: 1.35;
  color: var(--muted, #9ca3af);
}

.home-to-sort-highlight {
  color: var(--accent, #c4b8a5);
  font-weight: 600;
}

.home-to-sort-actions {
  grid-column: 2;
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 0.5rem 0.75rem;
}

.home-to-sort-primary {
  padding: 0.35rem 0.75rem;
  font-size: 0.88rem;
}

.home-to-sort-secondary {
  border: 0;
  background: transparent;
  color: var(--muted, #9ca3af);
  cursor: pointer;
  font: inherit;
  font-size: 0.88rem;
  padding: 0.25rem 0.35rem;
}

.home-to-sort-secondary:hover:not(:disabled),
.home-to-sort-secondary:focus-visible {
  color: var(--accent, #c4b8a5);
}

.home-to-sort-footer {
  margin-top: 1rem;
  font-size: 0.82rem;
}

@media (min-width: 720px) {
  .home-to-sort-row {
    grid-template-columns: auto 1fr auto;
    grid-template-rows: auto;
    align-items: center;
  }

  .home-to-sort-poster {
    grid-row: auto;
  }

  .home-to-sort-actions {
    grid-column: 3;
    justify-content: flex-end;
  }
}
</style>