<script setup lang="ts">
import { computed, onMounted, ref } from "vue";

import { api } from "../api";
import { sourceDisplayLabel } from "../sourceLabels";
import { mergeCommaOrderPreservingHidden, settingCommaTokens } from "../settingsCoerce";

const props = defineProps<{
  modelValue: string;
  requireOne?: boolean;
  /** Ordre seulement : pas de case à cocher (activation dans chaque bloc source). */
  orderOnly?: boolean;
  /** Si défini, seules ces sources sont affichées (les autres restent dans modelValue). */
  visibleIds?: string[] | null;
}>();

const emit = defineEmits<{ "update:modelValue": [string] }>();

interface ProviderType {
  type: string;
  capabilities: string[];
  download_rank: number | null;
}

interface Row {
  id: string;
  label: string;
  enabled: boolean;
}

const fallback = [
  "wawacity",
  "free_telecharger",
  "hive",
  "comet",
  "torrentio",
  "stremio_addons",
].map((id) => ({ id, label: sourceDisplayLabel(id) }));

const catalog = ref(fallback);
const dragId = ref<string | null>(null);

onMounted(async () => {
  try {
    const page = await api<{ items: ProviderType[] }>("/api/v1/providers/types?kind=source");
    const download = page.items
      .filter(
        (item) =>
          item.capabilities.includes("links") ||
          item.capabilities.includes("episode") ||
          item.capabilities.includes("hive"),
      )
      .sort(
        (left, right) =>
          (left.download_rank ?? 1000) - (right.download_rank ?? 1000) || left.type.localeCompare(right.type),
      );
    if (download.length) {
      catalog.value = download.map((item) => ({
        id: item.type,
        label: sourceDisplayLabel(item.type),
      }));
    }
  } catch {
    catalog.value = fallback;
  }
});

const allowedIds = computed(() => {
  if (props.visibleIds != null) {
    return new Set(props.visibleIds);
  }
  return new Set(catalog.value.map((item) => item.id));
});

const rows = computed<Row[]>(() => {
  const known = allowedIds.value;
  const catalogVisible = catalog.value.filter((item) => known.has(item.id));
  const enabled = settingCommaTokens(props.modelValue).filter((part) => known.has(part));
  const order = [...enabled, ...catalogVisible.map((item) => item.id).filter((id) => !enabled.includes(id))];
  const labels = new Map(catalogVisible.map((item) => [item.id, item.label]));
  return order.map((id) => ({
    id,
    label: sourceDisplayLabel(id, labels.get(id)),
    enabled: enabled.includes(id),
  }));
});

function hiddenEnabledTokens(): string[] {
  const visible = allowedIds.value;
  return settingCommaTokens(props.modelValue).filter((id) => !visible.has(id));
}

function commit(next: Row[]) {
  const ordered = next.map((row) => row.id);
  const hidden = hiddenEnabledTokens();
  if (props.orderOnly) {
    emit("update:modelValue", mergeCommaOrderPreservingHidden(props.modelValue, allowedIds.value, ordered));
    return;
  }
  const visibleEnabled = next
    .filter((row) => row.enabled)
    .map((row) => row.id);
  emit("update:modelValue", [...visibleEnabled, ...hidden].join(","));
}

function toggle(id: string) {
  const current = rows.value;
  const target = current.find((row) => row.id === id);
  if (!target) {
    return;
  }
  if (props.requireOne && target.enabled && current.filter((row) => row.enabled).length === 1) {
    return;
  }
  commit(current.map((row) => (row.id === id ? { ...row, enabled: !row.enabled } : row)));
}

function move(index: number, delta: number) {
  const next = [...rows.value];
  const target = index + delta;
  if (target < 0 || target >= next.length) {
    return;
  }
  if (!next[index].enabled || !next[target].enabled) {
    return;
  }
  const [item] = next.splice(index, 1);
  next.splice(target, 0, item);
  commit(next);
}

function onDragStart(row: Row, event: DragEvent) {
  if (!row.enabled) {
    event.preventDefault();
    return;
  }
  dragId.value = row.id;
}

function onDrop(targetId: string) {
  const from = dragId.value;
  dragId.value = null;
  if (!from || from === targetId) {
    return;
  }
  const next = [...rows.value];
  const origin = next.findIndex((row) => row.id === from);
  const target = next.findIndex((row) => row.id === targetId);
  if (origin < 0 || target < 0 || !next[origin].enabled || !next[target].enabled) {
    return;
  }
  const [item] = next.splice(origin, 1);
  next.splice(target, 0, item);
  commit(next);
}
</script>

<template>
  <ol class="rank-list">
    <li
      v-for="(row, index) in rows"
      :key="row.id"
      :class="{ 'is-off': !row.enabled }"
      :draggable="row.enabled"
      @dragstart="onDragStart(row, $event)"
      @dragover.prevent
      @drop="onDrop(row.id)"
    >
      <span class="rank-handle" aria-hidden="true">⠿</span>
      <span v-if="orderOnly" class="rank-label">{{ row.label }}</span>
      <label v-else class="switch-line compact">
        <span>{{ row.label }}</span>
        <span class="switch switch-small">
          <input type="checkbox" :checked="row.enabled" @change="toggle(row.id)" />
          <span class="switch-ui" aria-hidden="true"></span>
        </span>
      </label>
      <span v-if="!row.enabled" class="rank-off">désactivée</span>
      <span class="rank-moves">
        <button
          type="button"
          :disabled="index === 0 || !row.enabled || (index > 0 && !rows[index - 1]?.enabled)"
          @click="move(index, -1)"
        >
          ↑
        </button>
        <button
          type="button"
          :disabled="
            index === rows.length - 1 ||
            !row.enabled ||
            (index < rows.length - 1 && !rows[index + 1]?.enabled)
          "
          @click="move(index, 1)"
        >
          ↓
        </button>
      </span>
    </li>
  </ol>
</template>
