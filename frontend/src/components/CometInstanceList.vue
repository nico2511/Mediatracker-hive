<script setup lang="ts">
import { computed, ref, watch } from "vue";

import { cometInstanceUrlKey, normalizeCometInstanceUrl } from "../cometUrl";
import TruncatedUrlLine from "./TruncatedUrlLine.vue";

const props = defineProps<{
  modelValue: string[];
}>();

const emit = defineEmits<{ "update:modelValue": [string[]] }>();

const draft = ref("");

const rows = computed(() => props.modelValue);

watch(
  () => props.modelValue,
  () => {
    draft.value = "";
  },
);

function commit(next: string[]) {
  emit("update:modelValue", next);
}

function addUrl() {
  const text = draft.value.trim();
  if (!text) {
    return;
  }
  const normalized = normalizeCometInstanceUrl(text);
  if (!normalized) {
    return;
  }
  const key = cometInstanceUrlKey(normalized);
  if (rows.value.some((item) => cometInstanceUrlKey(item) === key)) {
    draft.value = "";
    return;
  }
  commit([...rows.value, normalized]);
  draft.value = "";
}

function removeAt(index: number) {
  const next = rows.value.filter((_, row) => row !== index);
  commit(next);
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
</script>

<template>
  <div class="comet-instance-list stack">
    <p class="hint">
      Instances Comet interrogées dans cet ordre. Sans clé débrideur : configuration publique ou instance perso. Vide :
      Comet est ignoré.
    </p>
    <ol v-if="rows.length" class="comet-instance-rows">
      <li v-for="(url, index) in rows" :key="url + ':' + index" class="comet-instance-row comet-instance-row--url">
        <span class="comet-instance-rank" aria-hidden="true">{{ index + 1 }}</span>
        <TruncatedUrlLine :url="url" />
        <span v-if="index === 0" class="comet-instance-badge">utilisée en premier</span>
        <span class="comet-instance-moves">
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
      </li>
    </ol>
    <p v-else class="hint">Aucune instance : ajoutez une adresse ci-dessous.</p>
    <label class="comet-instance-add">
      <span>Ajouter une instance</span>
      <input
        v-model="draft"
        type="text"
        inputmode="url"
        spellcheck="false"
        autocapitalize="off"
        autocorrect="off"
        placeholder="https://comet.exemple"
        @keydown.enter.prevent="addUrl"
      />
    </label>
    <button type="button" :disabled="!draft.trim()" @click="addUrl">Ajouter</button>
  </div>
</template>