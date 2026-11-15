<script setup lang="ts">
import { ref } from "vue";

import { api, userFacingApiError } from "../../api";
import type { HiveReceivedRequestItem } from "../../hive/hiveRequests";
import { hiveRequestTitleLine } from "../../hive/hiveRequests";
import { mediaKind } from "../../labels";

const props = defineProps<{
  rows: HiveReceivedRequestItem[];
}>();

const emit = defineEmits<{
  changed: [];
  error: [message: string];
  note: [message: string];
}>();

const busyId = ref<string | null>(null);

function metaLine(row: HiveReceivedRequestItem): string {
  const kind = mediaKind(row.type);
  if (row.already_shared) {
    return `${kind} · déjà partagé dans la ruche`;
  }
  return `${kind} · fichier local, non partagé`;
}

async function acceptRow(row: HiveReceivedRequestItem) {
  busyId.value = row.request_id;
  try {
    await api("/api/v1/hive/requests/received/accept", {
      method: "POST",
      body: JSON.stringify({ request_id: row.request_id }),
    });
    emit("note", "Demande acceptée : partage et transfert lancés si possible.");
    emit("changed");
  } catch (err) {
    emit("error", userFacingApiError(err, "Acceptation impossible."));
  } finally {
    busyId.value = null;
  }
}

async function refuseRow(row: HiveReceivedRequestItem) {
  busyId.value = row.request_id;
  try {
    await api("/api/v1/hive/requests/received/refuse", {
      method: "POST",
      body: JSON.stringify({ request_id: row.request_id }),
    });
    emit("note", "Demande refusée.");
    emit("changed");
  } catch (err) {
    emit("error", userFacingApiError(err, "Refus impossible."));
  } finally {
    busyId.value = null;
  }
}
</script>

<template>
  <ul v-if="rows.length" class="hive-received-list" data-testid="hive-received-list">
    <li v-for="row in rows" :key="row.request_id" class="hive-received-item panel">
      <div class="hive-received-body stack tight">
        <strong>{{ hiveRequestTitleLine(row) }}</strong>
        <span class="hint">{{ metaLine(row) }}</span>
        <p class="hive-received-anon hint">
          <span class="pill muted">Demande anonyme</span>
          <span v-if="row.already_shared" class="pill honey">Déjà partagé</span>
        </p>
        <p v-if="row.already_shared" class="hint">
          La demande peut être acceptée pour ouvrir un transfert.
        </p>
        <p v-else class="hint">Quelqu’un dans la ruche cherche ce fichier, sans révéler qui.</p>
      </div>
      <div class="hive-received-actions row">
        <button
          type="button"
          class="btn btn-sm primary"
          data-testid="hive-accept-request"
          :disabled="busyId === row.request_id"
          @click="acceptRow(row)"
        >
          Accepter
        </button>
        <button
          type="button"
          class="btn btn-sm danger"
          data-testid="hive-refuse-request"
          :disabled="busyId === row.request_id"
          @click="refuseRow(row)"
        >
          Refuser
        </button>
      </div>
    </li>
  </ul>
  <p v-else class="hint" data-testid="hive-received-empty">Aucune demande reçue pour l’instant.</p>
</template>

<style scoped>
.hive-received-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: grid;
  gap: 0.65rem;
}

.hive-received-item {
  display: flex;
  flex-wrap: wrap;
  align-items: flex-start;
  justify-content: space-between;
  gap: 0.75rem;
  padding: 0.75rem 0.85rem;
}

.hive-received-body {
  flex: 1 1 14rem;
  min-width: 0;
}

.hive-received-anon {
  display: flex;
  flex-wrap: wrap;
  gap: 0.35rem;
  align-items: center;
  margin: 0.25rem 0 0;
}

.hive-received-actions {
  gap: 0.5rem;
  flex-wrap: wrap;
  align-items: center;
}

.stack.tight {
  gap: 0.2rem;
}

.pill.honey {
  background: color-mix(in srgb, var(--honey, #d9b46a) 22%, transparent);
  border: 1px solid color-mix(in srgb, var(--honey, #d9b46a) 40%, transparent);
}
</style>