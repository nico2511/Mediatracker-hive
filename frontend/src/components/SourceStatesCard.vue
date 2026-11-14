<script setup lang="ts">
import { computed, onMounted, ref } from "vue";

import {api, userFacingApiError} from "../api";
import { formatParis } from "../time";

interface TargetRow {
  key: string;
  kind: string;
  label: string;
  state: string;
  message: string;
  since: string;
  last_debrid_at?: string | null;
}

interface ChangeRow {
  key: string;
  kind: string;
  label: string;
  state: string;
  message: string;
  changed_at: string;
}

const targets = ref<TargetRow[]>([]);
const changes = ref<ChangeRow[]>([]);
const error = ref("");

const sourceTargets = computed(() => targets.value.filter((row) => row.kind === "source"));
const hosterTargets = computed(() => targets.value.filter((row) => row.kind === "hoster"));

const states: Record<string, string> = {
  ok: "en ligne",
  down: "en panne",
};

function statusClass(status: string): string {
  if (status === "ok") {
    return "health-dot health-dot-ok";
  }
  if (status === "down") {
    return "health-dot health-dot-danger";
  }
  return "health-dot health-dot-muted";
}

function sinceLine(row: TargetRow): string {
  return `Depuis ${formatParis(row.since)}.`;
}

function outageDetail(row: TargetRow): string | null {
  if (row.state === "ok") {
    return null;
  }
  const text = row.message.trim();
  return text || null;
}

function lastDebridLine(row: TargetRow): string {
  if (row.last_debrid_at) {
    return `Dernier débridage : ${formatParis(row.last_debrid_at)}.`;
  }
  return "Dernier débridage : jamais.";
}

async function load() {
  error.value = "";
  try {
    const body = await api<{ targets: TargetRow[]; changes: ChangeRow[] }>("/api/v1/health/sources");
    targets.value = body.targets;
    changes.value = body.changes.slice(0, 8);
  } catch (err) {
    error.value = userFacingApiError(err, "État des sources indisponible.");
  }
}

onMounted(() => {
  void load();
});

defineExpose({ load });
</script>

<template>
  <section class="health-block">
    <h2>État des sources</h2>
    <p class="hint">Wawacity, Comet et hébergeurs AllDebrid suivis sur le trafic existant.</p>
    <p v-if="error" class="error">{{ error }}</p>
    <template v-if="targets.length">
      <ul v-if="sourceTargets.length" class="health-components">
        <li v-for="row in sourceTargets" :key="row.key" class="health-component-row">
          <div class="health-component-copy">
            <strong>{{ row.label }}</strong>
            <p class="hint">{{ sinceLine(row) }}</p>
            <p v-if="outageDetail(row)" class="hint">{{ outageDetail(row) }}</p>
          </div>
          <span class="health-component-state">
            <span :class="statusClass(row.state)" aria-hidden="true" />
            {{ states[row.state] || row.state }}
          </span>
        </li>
      </ul>
      <template v-if="hosterTargets.length">
        <h3 class="health-section-title">Hébergeurs (via AllDebrid)</h3>
        <ul class="health-components">
          <li v-for="row in hosterTargets" :key="row.key" class="health-component-row">
            <div class="health-component-copy">
              <strong>{{ row.label }}</strong>
              <p class="hint">{{ sinceLine(row) }}</p>
              <p v-if="outageDetail(row)" class="hint">{{ outageDetail(row) }}</p>
              <p class="hint">{{ lastDebridLine(row) }}</p>
            </div>
            <span class="health-component-state">
              <span :class="statusClass(row.state)" aria-hidden="true" />
              {{ states[row.state] || row.state }}
            </span>
          </li>
        </ul>
      </template>
    </template>
    <p v-else-if="!error" class="hint">Aucun changement enregistré pour le moment.</p>
    <details v-if="changes.length" class="health-history">
      <summary>Derniers changements</summary>
      <ul class="health-source-changes hint">
        <li v-for="change in changes" :key="`${change.key}-${change.changed_at}`">
          <span>{{ formatParis(change.changed_at) }}</span>
          <span>{{ change.label }} — {{ states[change.state] || change.state }}</span>
        </li>
      </ul>
    </details>
  </section>
</template>
