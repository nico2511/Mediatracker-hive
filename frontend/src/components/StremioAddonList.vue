<script setup lang="ts">
import { computed, ref, watch } from "vue";

import { cometInstanceUrlKey, normalizeCometInstanceUrl } from "../cometUrl";
import TruncatedUrlLine from "./TruncatedUrlLine.vue";

export type StremioAddonRow = {
  url: string;
  enabled: boolean;
};

const props = defineProps<{
  modelValue: StremioAddonRow[];
}>();

const emit = defineEmits<{ "update:modelValue": [StremioAddonRow[]] }>();

const draftUrl = ref("");

const rows = computed(() => props.modelValue);

watch(
  () => props.modelValue,
  () => {
    draftUrl.value = "";
  },
);

function commit(next: StremioAddonRow[]) {
  emit("update:modelValue", next);
}

function addRow() {
  const text = draftUrl.value.trim();
  if (!text) {
    return;
  }
  const normalized = normalizeCometInstanceUrl(text);
  if (!normalized) {
    return;
  }
  const key = cometInstanceUrlKey(normalized);
  if (rows.value.some((item) => cometInstanceUrlKey(item.url) === key)) {
    draftUrl.value = "";
    return;
  }
  commit([...rows.value, { url: normalized, enabled: true }]);
  draftUrl.value = "";
}

function removeAt(index: number) {
  commit(rows.value.filter((_, row) => row !== index));
}

function move(index: number, delta: number) {
  const target = index + delta;
  if (target < 0 || target >= rows.value.length) {
    return;
  }
  const next = [...rows.value];
  const [item] = next.splice(index, 1);
  next.splice(target, 0, item);
  commit(next);
}

function toggleEnabled(index: number, on: boolean) {
  const next = [...rows.value];
  next[index] = { ...next[index], enabled: on };
  commit(next);
}

function addonHostLabel(url: string): string {
  try {
    return new URL(url).hostname || url;
  } catch {
    return url;
  }
}
</script>

<template>
  <div class="comet-instance-list stack">
    <p class="hint">
      Addons interrogés dans cet ordre (une requête par recherche et par domaine). Aucune clé AllDebrid ou autre n’est
      envoyée aux addons. Vide : la source est ignorée.
    </p>
    <ol v-if="rows.length" class="addon-list">
      <li v-for="(row, index) in rows" :key="row.url + ':' + index" class="addon-row">
        <span class="addon-rank" aria-hidden="true">{{ index + 1 }}</span>
        <div class="addon-row-main stack">
          <div class="addon-host">{{ addonHostLabel(row.url) }}</div>
          <TruncatedUrlLine :url="row.url" />
          <div class="addon-toolbar">
            <label class="switch-line comet-instance-addon-switch">
              <span>{{ row.enabled ? "Activé" : "Désactivé" }}</span>
              <span class="switch">
                <input
                  type="checkbox"
                  :checked="row.enabled"
                  @change="toggleEnabled(index, ($event.target as HTMLInputElement).checked)"
                />
                <span class="switch-ui" aria-hidden="true"></span>
              </span>
            </label>
            <span class="rank-moves comet-instance-moves">
              <button type="button" :disabled="index === 0" aria-label="Monter" @click="move(index, -1)">↑</button>
              <button
                type="button"
                :disabled="index === rows.length - 1"
                aria-label="Descendre"
                @click="move(index, 1)"
              >
                ↓
              </button>
              <button type="button" aria-label="Supprimer" @click="removeAt(index)">Supprimer</button>
            </span>
          </div>
        </div>
      </li>
    </ol>
    <p v-else class="hint">Aucun addon : ajoutez une adresse ci-dessous.</p>
    <label class="comet-instance-add addon-add">
      <span>URL de base de l’addon</span>
      <input
        v-model="draftUrl"
        type="text"
        inputmode="url"
        spellcheck="false"
        autocapitalize="off"
        autocorrect="off"
        placeholder="https://addon-fictif.example"
        @keydown.enter.prevent="addRow"
      />
    </label>
    <button type="button" :disabled="!draftUrl.trim()" @click="addRow">Ajouter</button>
  </div>
</template>