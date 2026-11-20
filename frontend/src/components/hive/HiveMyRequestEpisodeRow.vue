<script setup lang="ts">
import { computed } from "vue";

import { formatBytes } from "../../diskFormat";
import type { HiveMyRequestItem } from "../../hive/hiveRequests";
import {
  hiveRequestEpisodeLabel,
  hiveRequestKindLine,
  hiveRequestMineBadge,
  hiveRequestMineSubBadge,
} from "../../hive/hiveRequests";
import type { HiveMyRequestDownloadProgress } from "../../hive/hiveRequests";

const props = defineProps<{
  item: HiveMyRequestItem;
  download?: HiveMyRequestDownloadProgress;
  busy: boolean;
}>();

const emit = defineEmits<{
  announce: [];
  retract: [];
}>();

const episodeLabel = computed(() => hiveRequestEpisodeLabel(props.item.season, props.item.episode));

const transferPct = computed(() => Math.round(props.download?.progress ?? 0));

const transferLine = computed(() => {
  if (!props.download || props.item.status !== "transfer") {
    return "";
  }
  const done = formatBytes(props.download.done_bytes);
  const total = props.download.size ? formatBytes(props.download.size) : "—";
  return `${transferPct.value} % · ${done} sur ${total}`;
});
</script>

<template>
  <li class="hive-mine-episode panel inset" :data-testid="`hive-mine-episode-${item.media_item_id}`">
    <div class="hive-mine-episode-main stack tight">
      <strong v-if="episodeLabel" class="hive-mine-episode-label">{{ episodeLabel }}</strong>
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
        :aria-valuenow="download?.progress ?? 0"
        aria-valuemin="0"
        aria-valuemax="100"
      >
        <span class="pill ok">En cours de transfert</span>
        <div class="hive-mine-bar" aria-hidden="true">
          <i :style="{ width: `${download?.progress ?? 12}%` }" />
        </div>
        <span v-if="transferLine" class="hint">{{ transferLine }}</span>
      </div>
    </div>
    <div class="hive-mine-episode-actions">
      <button
        v-if="(item.status === 'prompt' || item.status === 'retracted') && item.hive_request_available"
        type="button"
        class="btn primary"
        data-testid="hive-mine-announce"
        :disabled="busy"
        @click="emit('announce')"
      >
        Demander à la ruche
      </button>
      <button
        v-else-if="item.status !== 'prompt' && item.status !== 'retracted'"
        type="button"
        class="btn"
        data-testid="hive-mine-retract"
        :disabled="busy"
        @click="emit('retract')"
      >
        Ne plus demander à la ruche
      </button>
    </div>
  </li>
</template>

<style scoped>
.hive-mine-episode {
  display: flex;
  flex-wrap: wrap;
  align-items: flex-start;
  justify-content: space-between;
  gap: 0.65rem;
  padding: 0.65rem 0.75rem;
}

.panel.inset {
  background: color-mix(in srgb, var(--panel, #141414) 88%, transparent);
  border: 1px solid var(--hair);
}

.hive-mine-episode-main {
  flex: 1 1 12rem;
  min-width: 0;
}

.hive-mine-episode-label {
  font-weight: 650;
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

.hive-mine-episode-actions {
  display: flex;
  flex-direction: column;
  gap: 0.45rem;
  min-width: min(100%, 14rem);
}

.stack.tight {
  gap: 0.2rem;
}
</style>
