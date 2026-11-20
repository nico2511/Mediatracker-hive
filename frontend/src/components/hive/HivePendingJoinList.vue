<script setup lang="ts">
import { ref, watch } from "vue";

import { api, userFacingApiError } from "../../api";
import { hiveControlCodesMatch } from "../../hive/hiveControlCode";

export interface PendingJoinRow {
  pending_id: number;
  invitee_instance_id: string;
  invitee_name: string | null;
  control_code: string | null;
}

const props = defineProps<{
  rucheIdHex: string;
  rows: PendingJoinRow[];
}>();

const emit = defineEmits<{
  changed: [];
  error: [message: string];
  note: [message: string];
}>();

const pendingCodeEntry = ref<Record<number, string>>({});
const pendingCodeAck = ref<Record<number, boolean>>({});
const busyPendingId = ref<number | null>(null);

function syncAcceptUi(rows: PendingJoinRow[]) {
  const ids = new Set(rows.map((row) => row.pending_id));
  const nextEntry: Record<number, string> = {};
  const nextAck: Record<number, boolean> = {};
  for (const id of ids) {
    if (pendingCodeEntry.value[id] !== undefined) {
      nextEntry[id] = pendingCodeEntry.value[id];
    }
    if (pendingCodeAck.value[id] !== undefined) {
      nextAck[id] = pendingCodeAck.value[id];
    }
  }
  pendingCodeEntry.value = nextEntry;
  pendingCodeAck.value = nextAck;
}

watch(
  () => props.rows,
  (rows) => syncAcceptUi(rows),
  { immediate: true },
);

function pendingAcceptReady(row: PendingJoinRow): boolean {
  if (!row.control_code) {
    return false;
  }
  if (!pendingCodeAck.value[row.pending_id]) {
    return false;
  }
  return hiveControlCodesMatch(pendingCodeEntry.value[row.pending_id] ?? "", row.control_code);
}

async function acceptPending(row: PendingJoinRow) {
  if (!pendingAcceptReady(row)) {
    emit("error", "Saisissez le code communiqué par l’invité et confirmez la comparaison avant d’accepter.");
    return;
  }
  busyPendingId.value = row.pending_id;
  try {
    await api("/api/v1/hive/join/accept", {
      method: "POST",
      body: JSON.stringify({ pending_id: row.pending_id }),
    });
    emit("note", "Demande acceptée. L’invité peut confirmer son adhésion.");
    emit("changed");
  } catch (err) {
    emit("error", userFacingApiError(err, "Acceptation impossible."));
  } finally {
    busyPendingId.value = null;
  }
}

async function rejectPending(row: PendingJoinRow) {
  busyPendingId.value = row.pending_id;
  try {
    await api("/api/v1/hive/join/reject", {
      method: "POST",
      body: JSON.stringify({ pending_id: row.pending_id }),
    });
    emit("note", "Demande refusée.");
    emit("changed");
  } catch (err) {
    emit("error", userFacingApiError(err, "Refus impossible."));
  } finally {
    busyPendingId.value = null;
  }
}
</script>

<template>
  <div class="hive-pending stack">
    <h3 class="settings-subsection-title">Demandes d’adhésion</h3>
    <p class="hint">Demandez le code à l’invité, saisissez-le puis confirmez avant d’accepter.</p>
    <ul v-if="rows.length" class="hive-pending-list" data-testid="hive-pending-list">
      <li v-for="p in rows" :key="p.pending_id" class="hive-pending-item stack">
        <div class="stack tight">
          <strong>{{ p.invitee_name || "Invité" }}</strong>
          <code class="token">{{ p.invitee_instance_id }}</code>
        </div>
        <label class="hive-field" :for="`hive-pending-code-${p.pending_id}`">
          <span class="label">Code communiqué par l’invité</span>
          <input
            :id="`hive-pending-code-${p.pending_id}`"
            v-model="pendingCodeEntry[p.pending_id]"
            class="input block hive-pending-code-input"
            type="text"
            maxlength="8"
            autocomplete="off"
            autocorrect="off"
            autocapitalize="off"
            spellcheck="false"
            :data-testid="`hive-pending-code-input-${p.pending_id}`"
          />
        </label>
        <label class="check row hive-pending-ack">
          <input
            v-model="pendingCodeAck[p.pending_id]"
            type="checkbox"
            :data-testid="`hive-pending-ack-${p.pending_id}`"
          />
          <span>Je confirme que ce code correspond à celui donné par l’invité.</span>
        </label>
        <div class="row hive-pending-actions">
          <button
            type="button"
            class="btn btn-sm primary"
            data-testid="hive-accept-pending"
            :disabled="!pendingAcceptReady(p) || busyPendingId === p.pending_id"
            @click="acceptPending(p)"
          >
            Accepter
          </button>
          <button
            type="button"
            class="btn btn-sm danger"
            data-testid="hive-reject-pending"
            :disabled="busyPendingId === p.pending_id"
            @click="rejectPending(p)"
          >
            Refuser
          </button>
        </div>
      </li>
    </ul>
    <p v-else class="hint" data-testid="hive-pending-empty">Aucune demande en attente.</p>
  </div>
</template>

<style scoped>
.hive-pending-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: grid;
  gap: 0.55rem;
}

.hive-pending-item {
  align-items: flex-start;
  padding: 0.65rem 0.75rem;
  border: 1px solid var(--hair);
  border-radius: var(--radius-sm);
  background: color-mix(in srgb, var(--bg) 90%, var(--card));
}

.hive-field {
  display: grid;
  gap: 0.35rem;
  margin: 0;
  width: 100%;
}

.hive-field .label {
  font-size: 0.82rem;
  color: var(--muted);
}

.hive-pending-code-input {
  font-family: ui-monospace, monospace;
  letter-spacing: 0.12em;
  text-transform: uppercase;
}

.hive-pending-ack {
  gap: 0.5rem;
  align-items: flex-start;
  font-size: 0.92rem;
}

.hive-pending-actions {
  gap: 0.5rem;
  flex-wrap: wrap;
}

.stack.tight {
  gap: 0.2rem;
}

.token {
  font-family: monospace;
  word-break: break-all;
  font-size: 0.85rem;
}
</style>
