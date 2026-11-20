<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue";
import { useRouter } from "vue-router";

import { api, userFacingApiError } from "../api";
import type { HiveShareUiState } from "../hive/hiveBadges";
import { hiveRucheLabel, type HiveRucheListRow } from "../hive/hiveRucheLabel";

const props = defineProps<{
  mediaItemId: number;
  mediaId?: number | null;
  hiveEnabled: boolean;
  versionsTo?: string | null;
}>();

const emit = defineEmits<{ changed: [] }>();

const router = useRouter();
const state = ref<HiveShareUiState | null>(null);
const ruches = ref<HiveRucheListRow[]>([]);
const pickerOpen = ref(false);
const excluded = ref<Set<string>>(new Set());
const error = ref("");

const canShare = computed(() => Boolean(state.value?.has_file && !state.value?.pull_only));

async function load() {
  if (!props.hiveEnabled) {
    state.value = null;
    return;
  }
  try {
    state.value = await api<HiveShareUiState>(`/api/v1/hive/items/${props.mediaItemId}/sharing`);
    ruches.value = await api<HiveRucheListRow[]>("/api/v1/hive/ruches");
    excluded.value = new Set(state.value?.excluded_ruche_ids ?? []);
  } catch {
    state.value = null;
  }
}

onMounted(load);
watch(
  () => props.mediaItemId,
  () => void load(),
);

async function patchSharing(body: { exclude_all: boolean; excluded_ruche_ids: string[] }) {
  await api(`/api/v1/hive/items/${props.mediaItemId}/sharing`, {
    method: "PATCH",
    body: JSON.stringify(body),
  });
  await load();
  emit("changed");
}

async function shareAll() {
  try {
    await patchSharing({ exclude_all: false, excluded_ruche_ids: [] });
  } catch (err) {
    error.value = userFacingApiError(err, "Partage impossible.");
  }
}

async function unshareAll() {
  try {
    await patchSharing({ exclude_all: true, excluded_ruche_ids: [] });
  } catch (err) {
    error.value = userFacingApiError(err, "Impossible d’arrêter le partage.");
  }
}

function openRuchePicker() {
  pickerOpen.value = true;
}

async function applyRuches() {
  pickerOpen.value = false;
  try {
    await patchSharing({ exclude_all: false, excluded_ruche_ids: [...excluded.value] });
  } catch (err) {
    error.value = userFacingApiError(err, "Choix de ruches impossible.");
  }
}

function setRucheShare(rucheId: string, shareInRuche: boolean) {
  const next = new Set(excluded.value);
  if (shareInRuche) {
    next.delete(rucheId);
  } else {
    next.add(rucheId);
  }
  excluded.value = next;
}

async function toggleKeep() {
  if (!state.value) {
    return;
  }
  const next = !state.value.keep;
  try {
    await api(`/api/v1/items/${props.mediaItemId}`, { method: "PATCH", body: JSON.stringify({ keep: next }) });
    await load();
    emit("changed");
  } catch (err) {
    error.value = userFacingApiError(err, "Garder impossible.");
  }
}

function openVersions() {
  if (props.versionsTo) {
    void router.push(props.versionsTo);
    return;
  }
  if (props.mediaId) {
    void router.push({ path: `/medias/${props.mediaId}`, query: { reglages: "ouverts" } });
  }
}
</script>

<template>
  <template v-if="hiveEnabled && state">
    <button v-if="canShare && (state.exclude_all || !state.shared)" type="button" role="menuitem" @click="shareAll">
      Partager
    </button>
    <button v-if="canShare && state.shared && !state.exclude_all" type="button" role="menuitem" @click="unshareAll">
      Ne plus partager
    </button>
    <button v-if="canShare" type="button" role="menuitem" @click.stop="openRuchePicker">Choisir les ruches…</button>
    <button type="button" role="menuitem" @click="toggleKeep">
      {{ state.keep ? "Ne plus garder" : "Garder" }}
    </button>
    <button type="button" role="menuitem" @click="openVersions">Versions</button>
    <p v-if="error" class="hint" role="alert">{{ error }}</p>
  </template>

  <Teleport to="body">
    <div
      v-if="pickerOpen"
      class="hive-picker-backdrop"
      role="presentation"
      @click.self="pickerOpen = false"
    >
      <div
        class="hive-picker panel dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="hive-picker-title"
        @click.stop
      >
        <h3 id="hive-picker-title">Choisir les ruches</h3>
        <p class="hint">Décochez une ruche pour ne plus y partager cette fiche.</p>
        <div class="hive-picker-scroll">
          <ul class="hive-picker-list">
            <li v-for="r in ruches" :key="r.ruche_id_hex">
              <label class="hive-picker-row switch-line">
                <span>{{ hiveRucheLabel(r) }}</span>
                <span class="switch switch-small">
                  <input
                    type="checkbox"
                    :checked="!excluded.has(r.ruche_id_hex)"
                    @change="setRucheShare(r.ruche_id_hex, ($event.target as HTMLInputElement).checked)"
                  />
                  <span class="switch-ui" aria-hidden="true"></span>
                </span>
              </label>
            </li>
          </ul>
        </div>
        <div class="hive-picker-actions row">
          <button type="button" class="btn primary" @click="applyRuches">Appliquer</button>
          <button type="button" class="btn" @click="pickerOpen = false">Annuler</button>
        </div>
      </div>
    </div>
  </Teleport>
</template>

<style scoped>
.hive-picker-backdrop {
  position: fixed;
  inset: 0;
  z-index: 2500;
  display: grid;
  place-items: center;
  padding: var(--space-4);
  background: rgb(0 0 0 / 55%);
}

.hive-picker {
  display: flex;
  flex-direction: column;
  gap: 0.75rem;
  max-height: min(85vh, 32rem);
  width: min(440px, 100%);
  background: var(--card);
  border: 1px solid rgb(231 230 227 / 8%);
  box-shadow: 0 14px 36px rgb(0 0 0 / 50%);
}

.hive-picker-scroll {
  flex: 1 1 auto;
  min-height: 0;
  max-height: min(50vh, 20rem);
  overflow-y: auto;
  margin: 0 -0.25rem;
  padding: 0 0.25rem;
}

.hive-picker-list {
  list-style: none;
  margin: 0;
  padding: 0;
}

.hive-picker-row {
  align-items: center;
  gap: 0.5rem;
  padding: 0.35rem 0;
}

.hive-picker-actions {
  flex-shrink: 0;
  justify-content: flex-end;
  gap: 0.5rem;
  padding-top: 0.25rem;
  border-top: 1px solid rgb(231 230 227 / 10%);
}
</style>
