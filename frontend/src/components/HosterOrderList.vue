<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue";

import { api } from "../api";

const props = defineProps<{
  modelValue: string[];
}>();

const emit = defineEmits<{ "update:modelValue": [string[]] }>();

const catalog = ref<string[]>([]);
const blockedHosters = ref<string[]>([]);
const pickerReady = ref(false);
const pickerFailed = ref(false);
const draft = ref("");

const rows = computed(() => props.modelValue);

const addable = computed(() => {
  const chosen = new Set(rows.value.map((item) => item.toLowerCase()));
  return catalog.value.filter((name) => !chosen.has(name.toLowerCase()));
});

watch(
  () => props.modelValue,
  () => {
    draft.value = "";
  },
);

function commit(next: string[]) {
  emit("update:modelValue", next);
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

const blockedSet = computed(() => new Set(blockedHosters.value.map((item) => item.toLowerCase())));

function addName(name: string) {
  if (!pickerReady.value) {
    return;
  }
  const token = name.trim().toLowerCase();
  if (!token || blockedSet.value.has(token)) {
    return;
  }
  if (rows.value.some((item) => item.toLowerCase() === token)) {
    return;
  }
  const withoutLast = rows.value.filter((item) => item !== "1fichier");
  const next = token === "1fichier" ? [...withoutLast, token] : [...withoutLast, token, ...(rows.value.includes("1fichier") ? ["1fichier"] : [])];
  commit(next);
  draft.value = "";
}

onMounted(async () => {
  try {
    const body = await api<{ hosters: string[]; blocked_hosters?: string[] }>(
      "/api/v1/settings/sources/hosters",
    );
    catalog.value = body.hosters ?? [];
    blockedHosters.value = body.blocked_hosters ?? [];
    pickerReady.value = true;
  } catch {
    catalog.value = [];
    blockedHosters.value = [];
    pickerReady.value = false;
    pickerFailed.value = true;
  }
});
</script>

<template>
  <div class="comet-instance-list stack">
    <p class="hint">Les liens sont essayés dans cet ordre ; 1fichier reste toujours en dernier.</p>
    <ol v-if="rows.length" class="comet-instance-rows">
      <li v-for="(name, index) in rows" :key="name + ':' + index" class="comet-instance-row">
        <span class="comet-instance-rank" aria-hidden="true">{{ index + 1 }}</span>
        <span class="comet-instance-url">{{ name }}</span>
        <span class="comet-instance-moves">
          <button type="button" :disabled="index === 0" aria-label="Monter" @click="move(index, -1)">↑</button>
          <button
            type="button"
            :disabled="index === rows.length - 1 || name === '1fichier'"
            aria-label="Descendre"
            @click="move(index, 1)"
          >
            ↓
          </button>
          <button type="button" aria-label="Supprimer" :disabled="!rows.length" @click="removeAt(index)">Supprimer</button>
        </span>
      </li>
    </ol>
    <p v-else class="hint">Aucun hébergeur sélectionné.</p>
    <p v-if="pickerFailed" class="hint">Liste des hébergeurs indisponible.</p>
    <p v-else-if="!pickerReady" class="hint">Chargement des hébergeurs disponibles…</p>
    <label v-else-if="addable.length" class="comet-instance-add">
      <span>Ajouter un hébergeur</span>
      <select v-model="draft" :disabled="!pickerReady" @change="addName(draft)">
        <option value="">Choisir…</option>
        <option v-for="name in addable" :key="name" :value="name">{{ name }}</option>
      </select>
    </label>
  </div>
</template>