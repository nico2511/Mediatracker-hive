<script setup lang="ts">
import { onMounted, ref } from "vue";

import HiveReceivedRequestList from "../../components/hive/HiveReceivedRequestList.vue";
import { api, userFacingApiError } from "../../api";
import type { HiveReceivedRequestItem } from "../../hive/hiveRequests";
import { patchSettings } from "../../settings";

const items = ref<HiveReceivedRequestItem[]>([]);
const error = ref("");
const note = ref("");
const loading = ref(false);
const autoAccept = ref(false);
const autoBusy = ref(false);

async function loadSettings() {
  try {
    const page = await api<{ settings?: { key: string; value: unknown }[] }>("/api/v1/settings");
    const map = new Map((page.settings ?? []).map((row) => [row.key, row.value]));
    autoAccept.value = map.get("hive_auto_accept_requests") === true || map.get("hive_auto_accept_requests") === "true";
  } catch {
    autoAccept.value = false;
  }
}

async function load() {
  loading.value = true;
  error.value = "";
  try {
    const resp = await api<{ items: HiveReceivedRequestItem[] }>("/api/v1/hive/requests/received");
    items.value = resp.items ?? [];
  } catch (err) {
    error.value = userFacingApiError(err, "Impossible de charger les demandes reçues.");
    items.value = [];
  } finally {
    loading.value = false;
  }
}

async function onAutoAcceptToggle() {
  const next = autoAccept.value;
  autoBusy.value = true;
  note.value = "";
  error.value = "";
  try {
    await patchSettings({ values: { hive_auto_accept_requests: next } });
    note.value = "Réglage enregistré.";
  } catch (err) {
    error.value = userFacingApiError(err, "Échec de l’enregistrement.");
    autoAccept.value = !next;
  } finally {
    autoBusy.value = false;
  }
}

onMounted(async () => {
  await loadSettings();
  await load();
});

defineExpose({ reload: load, itemCount: () => items.value.length });
</script>

<template>
  <section class="stack hive-received-view">
    <p class="panel hive-received-info hint">
      Ces demandes sont des suivis en attente, anonymes : vous ne voyez pas qui cherche. Votre bibliothèque n’est révélée
      qu’après acceptation, qui lance le partage habituel.
    </p>
    <label class="panel hive-received-auto switch-line sp">
      <span>
        <span class="label">Partager automatiquement les demandes · {{ autoAccept ? "Activé" : "Désactivé" }}</span>
        <span class="hint block">Si un membre demande un fichier que vous possédez, le partage se fait sans validation.</span>
      </span>
      <span class="switch">
        <input
          v-model="autoAccept"
          type="checkbox"
          data-testid="hive-auto-accept-inline"
          :disabled="autoBusy"
          @change="onAutoAcceptToggle"
        />
        <span class="switch-ui" aria-hidden="true"></span>
      </span>
    </label>
    <p v-if="note" class="ok">{{ note }}</p>
    <p v-if="error" class="error" role="alert">{{ error }}</p>
    <p v-else-if="loading" class="hint">Chargement…</p>
    <HiveReceivedRequestList
      v-else
      :rows="items"
      @changed="load"
      @error="(msg) => (error = msg)"
      @note="(msg) => (note = msg)"
    />
  </section>
</template>

<style scoped>
.hive-received-info {
  padding: 0.75rem 0.85rem;
  border-color: color-mix(in srgb, var(--warn) 35%, var(--border));
  background: color-mix(in srgb, var(--warn) 8%, var(--card));
}

.hive-received-auto {
  padding: 0.75rem 0.85rem;
  align-items: flex-start;
  gap: 0.75rem;
}

.hive-received-auto .label {
  font-weight: 600;
  display: block;
}

@media (max-width: 720px) {
  .hive-received-auto {
    flex-direction: column;
  }
}
</style>
