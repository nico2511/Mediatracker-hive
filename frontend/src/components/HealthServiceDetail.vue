<script setup lang="ts">
import { RouterLink } from "vue-router";

import DiskSpaceCard from "./DiskSpaceCard.vue";
import HealthHiveRucheCard from "./HealthHiveRucheCard.vue";
import { formatBytes } from "../diskFormat";
import type { HivePeerLoadResponse } from "../hive/hivePeerLoad";
import { formatParis } from "../time";
import { pillClass, type HealthService, type SourceTargetRow } from "../views/healthPageServices";

defineProps<{
  service: HealthService;
  aria2: { download_speed: number | null; num_active: number | null; num_waiting: number | null } | null;
  hosterTargets: SourceTargetRow[];
  sourceTargets: SourceTargetRow[];
  hivePeerLoad: HivePeerLoadResponse | null;
  hivePeerLoadError: string;
  retesting: boolean;
}>();

const emit = defineEmits<{ retest: [] }>();

function hosterLine(row: SourceTargetRow): string {
  const bits = [`Depuis ${formatParis(row.since)}`];
  if (row.last_debrid_at) {
    bits.push(`Dernier débridage : ${formatParis(row.last_debrid_at)}`);
  }
  return bits.join(" · ");
}
</script>

<template>
  <div class="health-svc-detail">
    <p v-if="service.status === 'down' || service.status === 'degraded'" class="hint">{{ service.message }}</p>
    <dl v-if="service.componentName === 'aria2' && aria2" class="health-svc-kv">
      <div><dt>Jobs actifs</dt><dd>{{ aria2.num_active ?? "—" }}</dd></div>
      <div><dt>File d'attente</dt><dd>{{ aria2.num_waiting ?? "—" }}</dd></div>
      <div>
        <dt>Vitesse</dt>
        <dd>{{ formatBytes(aria2.download_speed ?? null) }}/s</dd>
      </div>
    </dl>
    <template v-if="service.componentName === 'alldebrid' && hosterTargets.length">
      <p class="health-detail-label">Hébergeurs suivis</p>
      <ul class="health-hoster-list">
        <template v-for="hoster in hosterTargets" :key="hoster.key">
          <li>
            <span>{{ hoster.label }}</span>
            <span :class="pillClass(hoster.state === 'down' ? 'down' : 'ok')">
              {{ hoster.state === "down" ? "indisponible" : "en ligne" }}
            </span>
          </li>
          <li class="health-hoster-meta"><span class="hint">{{ hosterLine(hoster) }}</span></li>
        </template>
      </ul>
    </template>
    <template v-if="service.kind === 'source'">
      <p v-for="target in sourceTargets" :key="target.key" class="hint">
        {{ target.message || `Depuis ${formatParis(target.since)}.` }}
      </p>
    </template>
    <HealthHiveRucheCard
      v-if="service.id === 'hive'"
      :payload="hivePeerLoad"
      :error="hivePeerLoadError || undefined"
    />
    <DiskSpaceCard v-if="service.componentName === 'disk'" :panel="'full'" :heading="false" />
    <p class="actions health-svc-actions">
      <button type="button" class="primary" :disabled="retesting" @click.stop="emit('retest')">
        {{ retesting ? "Test…" : "Retester" }}
      </button>
      <RouterLink v-if="service.componentName === 'aria2'" class="button-link" to="/telechargements" @click.stop>
        Ouvrir les téléchargements
      </RouterLink>
      <RouterLink v-else-if="service.componentName === 'library'" class="button-link" to="/bibliotheque" @click.stop>
        Ouvrir la bibliothèque
      </RouterLink>
      <RouterLink
        v-else-if="service.componentName === 'jellyfin' || service.componentName === 'alldebrid'"
        class="button-link"
        to="/reglages/jetons"
        @click.stop
      >
        Ouvrir les réglages
      </RouterLink>
      <RouterLink v-else-if="service.id === 'update'" class="button-link" to="/reglages/maj" @click.stop>
        Ouvrir les réglages
      </RouterLink>
    </p>
  </div>
</template>
