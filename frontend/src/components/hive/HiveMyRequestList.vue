<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { RouterLink } from "vue-router";

import { api, userFacingApiError } from "../../api";
import { formatBytes } from "../../diskFormat";
import {
  defaultExpandedMyRequestGroups,
  groupHiveMyRequests,
  hiveMyRequestAggregateLine,
  hiveMyRequestSeriesSubtitle,
  hiveRequestKindLine,
  hiveRequestMineBadge,
  hiveRequestMineSubBadge,
  type HiveMyRequestDownloadProgress,
  type HiveMyRequestGroup,
  type HiveMyRequestItem,
} from "../../hive/hiveRequests";
import { mediaKind } from "../../labels";
import TmdbPosterImg from "../TmdbPosterImg.vue";
import HiveMyRequestEpisodeRow from "./HiveMyRequestEpisodeRow.vue";

export type DownloadProgressRow = HiveMyRequestDownloadProgress;

const props = defineProps<{
  items: HiveMyRequestItem[];
  downloads?: HiveMyRequestDownloadProgress[];
}>();

const emit = defineEmits<{ changed: [] }>();

const busyItem = ref<number | null>(null);
const error = ref("");
const expanded = ref<Record<number, boolean>>({});

const downloadByItem = computed(() => {
  const map = new Map<number, HiveMyRequestDownloadProgress>();
  for (const job of props.downloads ?? []) {
    if (job.media_item_id != null && !map.has(job.media_item_id)) {
      map.set(job.media_item_id, job);
    }
  }
  return map;
});

const groups = computed(() => groupHiveMyRequests(props.items));

watch(
  groups,
  (next) => {
    const defaults = defaultExpandedMyRequestGroups(next);
    for (const group of next) {
      if (expanded.value[group.media_id] === undefined) {
        expanded.value[group.media_id] = defaults[group.media_id] ?? false;
      }
    }
  },
  { immediate: true },
);

function isSeriesGroup(group: HiveMyRequestGroup): boolean {
  return group.type === "series";
}

function toggleGroup(mediaId: number) {
  expanded.value[mediaId] = !expanded.value[mediaId];
}

function filmItem(group: HiveMyRequestGroup): HiveMyRequestItem {
  return group.episodes[0];
}

function transferLine(item: HiveMyRequestItem): string {
  const job = downloadByItem.value.get(item.media_item_id);
  if (!job || item.status !== "transfer") {
    return "";
  }
  const pct = Math.round(job.progress);
  const done = formatBytes(job.done_bytes);
  const total = job.size ? formatBytes(job.size) : "—";
  return `${pct} % · ${done} sur ${total}`;
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
  <div class="stack hive-mine-root">
    <p v-if="error" class="error" role="alert">{{ error }}</p>
    <ul v-if="groups.length" class="hive-mine-list" data-testid="hive-mine-list">
      <li
        v-for="group in groups"
        :key="group.media_id"
        class="hive-mine-group panel"
        :data-testid="`hive-mine-group-${group.media_id}`"
      >
        <template v-if="isSeriesGroup(group)">
          <button
            type="button"
            class="hive-mine-group-toggle"
            :aria-expanded="expanded[group.media_id] ? 'true' : 'false'"
            :data-testid="`hive-mine-group-toggle-${group.media_id}`"
            @click="toggleGroup(group.media_id)"
          >
            <span
              class="hive-mine-poster"
              :class="{ placeholder: !group.poster_url }"
              aria-hidden="true"
            >
              <TmdbPosterImg
                v-if="group.poster_url"
                :url="group.poster_url"
                :alt="''"
                thumb
                lazy
              />
            </span>
            <span class="hive-mine-group-text stack tight">
              <RouterLink
                class="hive-mine-title"
                :to="`/medias/${group.media_id}`"
                @click.stop
              >
                {{ group.title }}
              </RouterLink>
              <span class="hint">{{ hiveMyRequestSeriesSubtitle(group.episodes.length) }}</span>
              <span v-if="hiveMyRequestAggregateLine(group.statusCounts)" class="hint hive-mine-aggregate">
                {{ hiveMyRequestAggregateLine(group.statusCounts) }}
              </span>
            </span>
            <span class="library-section-chevron" aria-hidden="true" />
          </button>
          <ul v-show="expanded[group.media_id]" class="hive-mine-episodes">
            <HiveMyRequestEpisodeRow
              v-for="ep in group.episodes"
              :key="ep.media_item_id"
              :item="ep"
              :download="downloadByItem.get(ep.media_item_id)"
              :busy="busyItem === ep.media_item_id"
              @announce="announce(ep)"
              @retract="retract(ep)"
            />
          </ul>
        </template>
        <article v-else class="hive-mine-film row sp" :data-testid="`hive-mine-film-${group.media_id}`">
          <span
            class="hive-mine-poster"
            :class="{ placeholder: !group.poster_url }"
            aria-hidden="true"
          >
            <TmdbPosterImg
              v-if="group.poster_url"
              :url="group.poster_url"
              :alt="''"
              thumb
              lazy
            />
          </span>
          <div class="hive-mine-film-body stack tight">
            <RouterLink class="hive-mine-title" :to="`/medias/${group.media_id}`">{{ group.title }}</RouterLink>
            <span class="hint">{{ hiveRequestKindLine(filmItem(group)) }}</span>
            <p v-if="hiveRequestMineBadge(filmItem(group).status)" class="hive-mine-badges">
              <span class="pill honey">{{ hiveRequestMineBadge(filmItem(group).status) }}</span>
              <span class="pill outline">{{ hiveRequestMineSubBadge(filmItem(group).status) }}</span>
            </p>
            <p v-else class="hint">Recherche habituelle sans résultat — annonce possible.</p>
            <div
              v-if="filmItem(group).status === 'transfer'"
              class="hive-mine-progress"
              role="progressbar"
              :aria-valuenow="downloadByItem.get(filmItem(group).media_item_id)?.progress ?? 0"
              aria-valuemin="0"
              aria-valuemax="100"
            >
              <span class="pill ok">En cours de transfert</span>
              <div class="hive-mine-bar" aria-hidden="true">
                <i
                  :style="{
                    width: `${downloadByItem.get(filmItem(group).media_item_id)?.progress ?? 12}%`,
                  }"
                />
              </div>
              <span v-if="transferLine(filmItem(group))" class="hint">{{ transferLine(filmItem(group)) }}</span>
            </div>
            <span class="hint">{{ mediaKind(group.type) }}</span>
          </div>
          <div class="hive-mine-film-actions">
            <button
              v-if="
                (filmItem(group).status === 'prompt' || filmItem(group).status === 'retracted') &&
                filmItem(group).hive_request_available
              "
              type="button"
              class="btn primary"
              data-testid="hive-mine-announce"
              :disabled="busyItem === filmItem(group).media_item_id"
              @click="announce(filmItem(group))"
            >
              Demander à la ruche
            </button>
            <button
              v-else-if="filmItem(group).status !== 'prompt' && filmItem(group).status !== 'retracted'"
              type="button"
              class="btn"
              data-testid="hive-mine-retract"
              :disabled="busyItem === filmItem(group).media_item_id"
              @click="retract(filmItem(group))"
            >
              Ne plus demander à la ruche
            </button>
          </div>
        </article>
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
  gap: 0.75rem;
  max-width: 56rem;
}

.hive-mine-group {
  padding: 0;
  overflow: hidden;
}

.hive-mine-group-toggle {
  display: flex;
  align-items: flex-start;
  gap: 0.75rem;
  width: 100%;
  padding: 0.75rem 0.85rem;
  border: 0;
  background: transparent;
  color: inherit;
  text-align: left;
  cursor: pointer;
}

.hive-mine-poster {
  flex: none;
  width: 3.25rem;
  aspect-ratio: 2 / 3;
  border-radius: 4px;
  overflow: hidden;
  background: var(--hair);
  display: grid;
  place-items: center;
}

.hive-mine-poster.placeholder::after {
  content: "Affiche";
  font-size: 0.62rem;
  color: var(--muted);
}

.hive-mine-poster :deep(img) {
  width: 100%;
  height: 100%;
  object-fit: cover;
  display: block;
}

.hive-mine-group-text {
  flex: 1 1 auto;
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

.hive-mine-aggregate {
  line-height: 1.35;
}

.hive-mine-episodes {
  list-style: none;
  margin: 0;
  padding: 0 0.5rem 0.65rem;
  display: grid;
  gap: 0.45rem;
}

.hive-mine-film {
  align-items: flex-start;
  padding: 0.75rem 0.85rem;
  gap: 0.75rem;
}

.hive-mine-film-body {
  flex: 1 1 14rem;
  min-width: 0;
}

.hive-mine-film-actions {
  display: flex;
  flex-direction: column;
  gap: 0.45rem;
  min-width: min(100%, 14rem);
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

@media (min-width: 720px) {
  .hive-mine-list {
    gap: 0.85rem;
  }

  .hive-mine-poster {
    width: 4rem;
  }
}
</style>
