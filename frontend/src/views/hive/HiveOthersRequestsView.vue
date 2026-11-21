<script setup lang="ts">
import { onMounted, ref } from "vue";

import HiveReceivedRequestList from "../../components/hive/HiveReceivedRequestList.vue";
import HiveWantedVolunteerList from "../../components/hive/HiveWantedVolunteerList.vue";
import { api, userFacingApiError } from "../../api";
import type { HiveReceivedRequestItem, HiveWantedRequestItem } from "../../hive/hiveRequests";
import { patchSettings } from "../../settings";

const receivedItems = ref<HiveReceivedRequestItem[]>([]);
const wantedItems = ref<HiveWantedRequestItem[]>([]);
const error = ref("");
const note = ref("");
const noteCompact = ref("");

function setNote(message: string | { text: string; compact: string }) {
  if (typeof message === "string") {
    note.value = message;
    noteCompact.value = message;
    return;
  }
  note.value = message.text;
  noteCompact.value = message.compact;
}
const loading = ref(false);
const autoAccept = ref(false);
const autoTakeWanted = ref(false);
const autoBusy = ref(false);

async function loadSettings() {
  try {
    const page = await api<{ settings?: { key: string; value: unknown }[] }>("/api/v1/settings");
    const map = new Map((page.settings ?? []).map((row) => [row.key, row.value]));
    autoAccept.value = map.get("hive_auto_accept_requests") === true || map.get("hive_auto_accept_requests") === "true";
    autoTakeWanted.value = map.get("hive_auto_take_wanted") === true || map.get("hive_auto_take_wanted") === "true";
  } catch {
    autoAccept.value = false;
    autoTakeWanted.value = false;
  }
}

async function loadLists(): Promise<HiveReceivedRequestItem[]> {
  if (receivedItems.value.length === 0 && wantedItems.value.length === 0) {
    loading.value = true;
  }
  error.value = "";
  try {
    const [received, wanted] = await Promise.all([
      api<{ items: HiveReceivedRequestItem[] }>("/api/v1/hive/requests/received"),
      api<{ items: HiveWantedRequestItem[] }>("/api/v1/hive/requests/wanted"),
    ]);
    receivedItems.value = received.items ?? [];
    wantedItems.value = wanted.items ?? [];
  } catch (err) {
    error.value = userFacingApiError(err, "Impossible de charger les demandes des autres.");
    receivedItems.value = [];
    wantedItems.value = [];
  } finally {
    loading.value = false;
  }
  return receivedItems.value;
}

async function load() {
  await Promise.all([loadSettings(), loadLists()]);
}

async function persistSetting(key: "hive_auto_accept_requests" | "hive_auto_take_wanted", next: boolean, revert: () => void) {
  autoBusy.value = true;
  note.value = "";
  noteCompact.value = "";
  error.value = "";
  try {
    await patchSettings({ values: { [key]: next } });
    setNote("Réglage enregistré.");
    if (key === "hive_auto_accept_requests") {
      await loadLists();
    }
  } catch (err) {
    error.value = userFacingApiError(err, "Échec de l’enregistrement.");
    revert();
  } finally {
    autoBusy.value = false;
  }
}

async function onAutoAcceptToggle() {
  const next = autoAccept.value;
  const previous = !next;
  await persistSetting("hive_auto_accept_requests", next, () => {
    autoAccept.value = previous;
  });
}

async function onAutoTakeWantedToggle() {
  const next = autoTakeWanted.value;
  const previous = !next;
  await persistSetting("hive_auto_take_wanted", next, () => {
    autoTakeWanted.value = previous;
  });
}

onMounted(load);

defineExpose({
  reload: load,
  itemCount: () => receivedItems.value.length + wantedItems.value.length,
});
</script>

<template>
  <section class="stack hive-others-view">
    <div class="hive-others-toggles stack">
      <label class="panel hive-others-toggle switch-line sp">
        <span>
          <span class="label"
            >Accepter automatiquement les demandes de partage · {{ autoAccept ? "Activé" : "Désactivé" }}</span
          >
          <span class="hint block"
            >Si un membre demande un fichier que vous possédez, le partage se fait sans validation.</span
          >
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
      <label class="panel hive-others-toggle switch-line sp">
        <span>
          <span class="label"
            >Prendre en charge automatiquement les recherches · {{ autoTakeWanted ? "Activé" : "Désactivé" }}</span
          >
          <span class="hint block hint-phone">Désactivé par défaut. Activé, cela lance de vrais téléchargements.</span>
          <span class="hint block hint-desktop"
            >Désactivé par défaut. Activé, les recherches encore sans fichier sont prises en charge comme « Je m’en occupe » : suivi, vrais téléchargements par la chaîne habituelle, puis partage à l’arrivée.</span
          >
        </span>
        <span class="switch">
          <input
            v-model="autoTakeWanted"
            type="checkbox"
            data-testid="hive-auto-take-wanted-inline"
            :disabled="autoBusy"
            @change="onAutoTakeWantedToggle"
          />
          <span class="switch-ui" aria-hidden="true"></span>
        </span>
      </label>
    </div>
    <p v-if="note" class="ok">
      <span class="note-phone">{{ noteCompact }}</span>
      <span class="note-desktop">{{ note }}</span>
    </p>
    <p v-if="error" class="error" role="alert">{{ error }}</p>
    <p v-else-if="loading" class="hint">Chargement…</p>
    <template v-else>
      <h2 class="settings-subsection-title">Vous l’avez</h2>
      <HiveReceivedRequestList
        :rows="receivedItems"
        :auto-accept="autoAccept"
        :reload="loadLists"
        @error="(msg) => (error = msg)"
        @note="setNote"
      />
      <h2 class="settings-subsection-title hive-others-wanted-title">Personne ne l’a encore</h2>
      <HiveWantedVolunteerList
        :rows="wantedItems"
        @changed="loadLists"
        @error="(msg) => (error = msg)"
        @note="setNote"
      />
    </template>
  </section>
</template>

<style scoped>
.hive-others-toggle {
  padding: 0.75rem 0.85rem;
  align-items: flex-start;
  gap: 0.75rem;
  flex: 0 0 auto;
}

.hive-others-toggle .label {
  font-weight: 600;
  display: block;
}

.hive-others-toggles {
  gap: 0.65rem;
}

.hive-others-wanted-title {
  margin-top: 0.5rem;
}

.hive-others-toggle .hint-desktop {
  display: none;
}

@media (min-width: 721px) {
  .hive-others-toggle .hint-phone {
    display: none;
  }

  .hive-others-toggle .hint-desktop {
    display: block;
  }
}

.ok .note-desktop {
  display: none;
}

@media (min-width: 721px) {
  .ok .note-phone {
    display: none;
  }

  .ok .note-desktop {
    display: inline;
  }
}
</style>
