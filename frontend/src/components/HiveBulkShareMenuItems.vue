<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue";

import { api, userFacingApiError } from "../api";
import type { SharingScope } from "../hive/hiveEmpreintes";

const props = defineProps<{
  mediaId: number;
  season?: number | null;
  hiveEnabled: boolean;
}>();

const emit = defineEmits<{ changed: []; openEmpreintes: [] }>();

const scope = ref<SharingScope | null>(null);
const loadError = ref("");
const actionError = ref("");
const busy = ref(false);
const confirmUnshare = ref(false);
const loading = ref(false);

const basePath = computed(() => {
  if (props.season != null) {
    return `/api/v1/hive/media/${props.mediaId}/seasons/${props.season}`;
  }
  return `/api/v1/hive/media/${props.mediaId}`;
});

async function load() {
  if (!props.hiveEnabled) {
    scope.value = null;
    return;
  }
  loading.value = true;
  loadError.value = "";
  try {
    scope.value = await api<SharingScope>(`${basePath.value}/sharing-scope`);
  } catch (err) {
    scope.value = null;
    loadError.value = userFacingApiError(err, "Impossible de charger l’état de partage.");
  } finally {
    loading.value = false;
  }
}

onMounted(load);
watch(
  () => [props.mediaId, props.season],
  () => void load(),
);

async function patchSharing(excludeAll: boolean) {
  busy.value = true;
  actionError.value = "";
  try {
    await api(`${basePath.value}/sharing`, {
      method: "PATCH",
      body: JSON.stringify({ exclude_all: excludeAll, excluded_ruche_ids: [] }),
    });
    confirmUnshare.value = false;
    await load();
    emit("changed");
  } catch (err) {
    actionError.value = userFacingApiError(err, excludeAll ? "Impossible d’arrêter le partage." : "Partage impossible.");
  } finally {
    busy.value = false;
  }
}

const fileCount = computed(() => scope.value?.file_count ?? 0);
const scopeLabel = computed(() =>
  props.season != null
    ? `${fileCount.value} fichier${fileCount.value > 1 ? "s" : ""} de la saison`
    : `${fileCount.value} fichier${fileCount.value > 1 ? "s" : ""} de la fiche`,
);
</script>

<template>
  <template v-if="hiveEnabled">
    <p v-if="loading && !scope" class="hint">Chargement du partage…</p>
    <p v-else-if="loadError" class="error" role="alert">
      {{ loadError }}
      <button type="button" class="btn-link" @click="load">Réessayer</button>
    </p>
    <template v-else-if="scope">
      <p v-if="scope.message && !scope.can_act" class="hint" role="status">{{ scope.message }}</p>
      <button
        v-if="scope.can_act && !scope.any_shared && scope.file_count > 0"
        type="button"
        role="menuitem"
        :disabled="busy"
        @click="patchSharing(false)"
      >
        Partager
      </button>
      <button
        v-if="scope.can_act && scope.any_shared"
        type="button"
        role="menuitem"
        class="danger"
        :disabled="busy"
        @click="confirmUnshare = true"
      >
        Ne plus partager
      </button>
    </template>
    <button type="button" role="menuitem" @click="emit('openEmpreintes')">Détails des empreintes</button>
    <p v-if="actionError" class="error" role="alert">{{ actionError }}</p>
  </template>

  <Teleport to="body">
    <div
      v-if="confirmUnshare"
      class="hive-picker-backdrop"
      role="presentation"
      @click.self="confirmUnshare = false"
    >
      <div class="hive-picker panel dialog" role="dialog" aria-modal="true" aria-labelledby="unshare-title">
        <h3 id="unshare-title">Ne plus partager</h3>
        <p class="hint">
          {{ scopeLabel }} : retrait des annonces de cette instance uniquement. Les fichiers sur le disque et les pairs
          ne sont pas modifiés.
        </p>
        <div class="hive-picker-actions row">
          <button type="button" class="btn danger" :disabled="busy" @click="patchSharing(true)">Confirmer</button>
          <button type="button" class="btn" @click="confirmUnshare = false">Annuler</button>
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
  width: min(440px, 100%);
  padding: 1rem;
  background: var(--card);
  border: 1px solid rgb(231 230 227 / 8%);
}
.hive-picker-actions {
  justify-content: flex-end;
  gap: 0.5rem;
  margin-top: 0.75rem;
}
.btn-link {
  margin-left: 0.35rem;
  text-decoration: underline;
  background: none;
  border: 0;
  color: inherit;
  cursor: pointer;
}
</style>
