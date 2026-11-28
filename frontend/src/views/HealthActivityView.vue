<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue";
import { RouterLink, useRoute, useRouter } from "vue-router";

import { api, userFacingApiError } from "../api";
import type { MetroActivityBody, MetroLevel } from "../metro/types";
import MetroEdgePanel from "./metro/MetroEdgePanel.vue";
import MetroMapSvg from "./metro/MetroMapSvg.vue";
import MetroStationPanel from "./metro/MetroStationPanel.vue";

const HOURS = 24;

const route = useRoute();
const router = useRouter();

const loading = ref(true);
const error = ref("");
const body = ref<MetroActivityBody | null>(null);
const level = ref<MetroLevel>(2);
const selectedStationId = ref<string | null>("RNG5");
const selectedEdgeId = ref<string | null>(null);
const mobile = ref(false);
const expandedLine = ref<string | null>("Rangement");

const graph = computed(() => body.value?.graph ?? null);
const kpis = computed(() => body.value?.kpis);
const collection = computed(() => body.value?.collection);
const hasData = computed(() => body.value?.has_data ?? false);
const showEmpty = computed(
  () => !loading.value && !error.value && (!collection.value?.active || !hasData.value),
);

const litEdges = computed(() => body.value?.lit_edges ?? []);
const level1Edges = computed(() => body.value?.level1_edges ?? []);

const lineDisplayName: Record<string, string> = {
  Sources: "Sources",
  Suivi: "Suivi",
  AllDebrid: "AllDebrid",
  Telechargements: "Téléchargements",
  Rangement: "Rangement",
  Bibliotheque: "Bibliothèque",
  Jellyfin: "Jellyfin",
  TMDB: "TMDB",
  Ruche: "Ruche",
  Corbeille: "Corbeille",
  Notifications: "Notifications",
  Socle: "Socle",
};

function lineSummary(lineName: string): string {
  const stations = graph.value?.stations.filter((row) => row.line === lineName) ?? [];
  if (!body.value) {
    return `${stations.length} stations`;
  }
  const block = body.value.active_blocks.find((row) => {
    const st = graph.value?.stations.find((s) => s.id === row.station_id);
    return st?.line === lineName;
  });
  if (block) {
    return `blocage depuis ${block.minutes} min`;
  }
  const slow = body.value.slow_stations.find((row) => {
    const st = graph.value?.stations.find((s) => s.id === row.station_id);
    return st?.line === lineName;
  });
  if (slow) {
    return "station lente";
  }
  return `${stations.length} stations`;
}

const breadcrumb = computed(() => {
  if (level.value === 1) {
    return ["Réseau", "Lignes"];
  }
  if (level.value === 3) {
    return ["Réseau", "Rangement", "Finalisation"];
  }
  if (selectedEdgeId.value) {
    return ["Réseau", "Stations", `C${selectedEdgeId.value.slice(1)}`];
  }
  const station = graph.value?.stations.find((row) => row.id === selectedStationId.value);
  return ["Réseau", "Stations", station?.label ?? "—"];
});

const selectedStation = computed(() =>
  graph.value?.stations.find((row) => row.id === selectedStationId.value) ?? null,
);

const selectedEdge = computed(() =>
  graph.value?.edges.find((row) => row.id === selectedEdgeId.value) ?? null,
);

const blockBanner = computed(() => {
  const blocks = body.value?.active_blocks ?? [];
  if (!blocks.length) {
    return "";
  }
  const first = blocks[0]!;
  const edge = first.edge_id ? `C${first.edge_id.slice(1)}` : "";
  return `${blocks.length} blocage — ${first.blocked_by ?? first.station_id} depuis ${first.minutes} min${edge ? ` (${edge})` : ""}`;
});

function exitRowStats(edgeId: string) {
  const stats = body.value?.rng5_exit_stats?.[edgeId];
  return {
    passes_24h: stats?.passes_24h ?? 0,
    median_ms: stats?.median_ms ?? null,
  };
}

function formatDuration(ms: number | null | undefined): string {
  if (ms == null) {
    return "—";
  }
  if (ms >= 1000) {
    return `${(ms / 1000).toFixed(1)} s`;
  }
  return `${ms} ms`;
}

function formatCollectionUntil(): string {
  const until = collection.value?.until;
  if (!until || !collection.value?.active) {
    return "";
  }
  const end = new Date(until);
  const delta = end.getTime() - Date.now();
  if (delta <= 0) {
    return "arrêt imminent";
  }
  const hours = Math.floor(delta / 3_600_000);
  const minutes = Math.floor((delta % 3_600_000) / 60_000);
  if (hours > 0) {
    return `arrêt automatique dans ${hours} h ${minutes}`;
  }
  return `arrêt automatique dans ${minutes} min`;
}

async function load() {
  loading.value = true;
  error.value = "";
  try {
    body.value = await api<MetroActivityBody>(`/api/v1/metro/activity?hours=${HOURS}`);
  } catch (err) {
    error.value = userFacingApiError(err, "Activité réseau indisponible.");
    body.value = null;
  } finally {
    loading.value = false;
  }
}

function onSelectStation(id: string) {
  selectedEdgeId.value = null;
  selectedStationId.value = id;
  if (mobile.value) {
    void router.push({ name: "health-metro-station", params: { stationId: id } });
    return;
  }
  if (id === "RNG5") {
    level.value = 2;
  }
}

function onSelectEdge(id: string) {
  selectedEdgeId.value = id;
  selectedStationId.value = null;
  if (mobile.value) {
    void router.push({ name: "health-metro-correspondence", params: { edgeId: id } });
  }
}

function onSelectLine(lineName: string) {
  level.value = 2;
  selectedEdgeId.value = null;
  const line = graph.value?.lines.find((row) => row.name === lineName);
  const focus = line?.focus_station_id;
  if (focus) {
    selectedStationId.value = focus;
    return;
  }
  const first = graph.value?.stations.find((row) => row.line === lineName);
  selectedStationId.value = first?.id ?? null;
}

function enterStation() {
  if (selectedStationId.value === "RNG5") {
    level.value = 3;
  }
}

function syncMobile() {
  mobile.value = typeof window !== "undefined" && window.matchMedia("(max-width: 720px)").matches;
}

onMounted(() => {
  syncMobile();
  window.addEventListener("resize", syncMobile);
  void load();
});

watch(
  () => route.query.niveau,
  (value) => {
    const parsed = Number(value);
    if (parsed === 1 || parsed === 2 || parsed === 3) {
      level.value = parsed;
    }
  },
  { immediate: true },
);

watch(
  () => route.query.edge,
  (value) => {
    if (typeof value === "string" && value) {
      level.value = 2;
      selectedEdgeId.value = value.toUpperCase();
      selectedStationId.value = null;
    }
  },
  { immediate: true },
);

watch(
  () => route.query.station,
  (value) => {
    if (typeof value === "string" && value) {
      level.value = 2;
      selectedStationId.value = value.toUpperCase();
      selectedEdgeId.value = null;
    }
  },
  { immediate: true },
);
</script>

<template>
  <div class="metro-activity" data-testid="health-activity">
    <p v-if="error" class="error">{{ error }}</p>
    <p v-if="blockBanner && !showEmpty" class="metro-block-banner" role="status">{{ blockBanner }}</p>

    <p v-if="showEmpty" class="metro-empty panel">
      Aucun passage enregistré sur les dernières 24 h. Activez la
      <RouterLink to="/reglages/application">collecte des passages</RouterLink>
      dans Réglages › Application pour alimenter la carte (conservée 7 jours).
    </p>

    <template v-else-if="body && graph">
      <div class="kpi-strip" aria-label="Synthèse des 24 heures">
        <div class="kpi">
          <span class="kpi-value">{{ kpis?.trains_in_progress ?? 0 }}</span>
          <span class="kpi-label">rames en cours</span>
        </div>
        <div class="kpi" :class="{ 'is-danger': (kpis?.active_blocks ?? 0) > 0 }">
          <span class="kpi-value">{{ kpis?.active_blocks ?? 0 }}</span>
          <span class="kpi-label">blocage en cours</span>
        </div>
        <div class="kpi" :class="{ 'is-warn': (kpis?.slow_stations ?? 0) > 0 }">
          <span class="kpi-value">{{ kpis?.slow_stations ?? 0 }}</span>
          <span class="kpi-label">stations lentes</span>
        </div>
        <div class="kpi is-warn">
          <span class="kpi-value">{{ kpis?.network_anomalies ?? 0 }}</span>
          <span class="kpi-label">anomalies du réseau</span>
        </div>
        <div class="kpi">
          <span class="kpi-value">{{ kpis?.edge_passes_24h ?? 0 }}</span>
          <span class="kpi-label">correspondances / 24 h</span>
        </div>
        <div class="kpi">
          <span class="kpi-value">{{ kpis?.median_pass_s != null ? `${kpis.median_pass_s} s` : "—" }}</span>
          <span class="kpi-label">médiane d'une passe</span>
        </div>
        <div class="kpi">
          <span class="kpi-value">{{ kpis?.untraceable_links ?? graph.untraceable_count }}</span>
          <span class="kpi-label">liens non traçables</span>
        </div>
      </div>

      <div class="nav-row">
        <div class="row" style="gap: 0.9rem">
          <div class="levels" role="group" aria-label="Niveau de détail">
            <button type="button" class="level-btn" data-testid="metro-level-1" :class="{ on: level === 1 }" @click="level = 1; selectedEdgeId = null">
              <span class="n">1</span>Lignes
            </button>
            <button type="button" class="level-btn" data-testid="metro-level-2" :class="{ on: level === 2 }" @click="level = 2; selectedEdgeId = null; selectedStationId = 'RNG5'">
              <span class="n">2</span>Stations
            </button>
            <button type="button" class="level-btn" data-testid="metro-level-3" :class="{ on: level === 3 }" @click="level = 3; selectedStationId = 'RNG5'; selectedEdgeId = null">
              <span class="n">3</span>Mécanismes
            </button>
          </div>
          <nav class="crumbs" aria-label="Fil d'Ariane">
            <span v-for="(part, index) in breadcrumb" :key="part">
              <span v-if="index" class="sep">›</span>
              <span :class="{ cur: index === breadcrumb.length - 1 }">{{ part }}</span>
            </span>
          </nav>
        </div>
        <span class="collect">
          <span>Collecte</span>
          <b>{{ collection?.active ? "active" : "inactive" }}</b>
          <span v-if="collection?.active">· {{ formatCollectionUntil() }}</span>
        </span>
      </div>

      <div class="legend metro-legend" aria-label="Légende des correspondances">
        <span>⋯ alimente (toujours dessiné)</span>
        <span>→ déclenche</span>
        <span>= attend</span>
        <span>— bloque</span>
        <span>— allumée : passage emprunté</span>
        <span>? non traçable</span>
        <span>— anomalie</span>
      </div>

      <div v-if="!mobile" class="net-grid" :class="{ 'wide-panel': Boolean(selectedEdgeId) }">
        <div class="plan">
          <MetroMapSvg
            v-if="level <= 2"
            :graph="graph"
            :level="level"
            :lit-edges="litEdges"
            :selected-station-id="selectedStationId"
            :selected-edge-id="selectedEdgeId"
            :dim-others="Boolean(selectedEdgeId)"
            :edge-hits="body.edge_hits"
            :level1-edges="level1Edges"
            @select-station="onSelectStation"
            @select-edge="onSelectEdge"
            @select-line="onSelectLine"
          />
          <section v-else class="interior">
            <h2>Intérieur de la station Finalisation</h2>
            <div class="chain" role="list">
              <article v-for="mech in graph.rng5_mechanisms" :key="mech.id" class="mech" :class="{ pole: mech.pole }">
                <span class="dot" aria-hidden="true" />
                <b>{{ mech.label }}</b>
                <span class="m">{{ mech.id }} · {{ mech.code }}</span>
              </article>
            </div>
            <table class="tbl exits-table">
              <thead>
                <tr>
                  <th>Ordre</th>
                  <th>Correspondance</th>
                  <th class="r">Passages / 24 h</th>
                  <th class="r">Médiane</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="exit in graph.rng5_exits" :key="exit.order" :class="{ anom: exit.anomaly }">
                  <td>{{ exit.order }}</td>
                  <td>
                    {{ exit.display_dest ?? exit.dest }}
                    <span class="hint">{{ exit.edge_id }}</span>
                  </td>
                  <td class="r">{{ exitRowStats(exit.edge_id).passes_24h }}</td>
                  <td class="r">{{ formatDuration(exitRowStats(exit.edge_id).median_ms) }}</td>
                </tr>
              </tbody>
            </table>
          </section>
          <div class="plan-foot">
            <span>{{ graph.station_count_visible }} stations visibles sur {{ graph.station_count_total }}</span>
            <span>Conservé {{ graph.retention_days }} j · lecture seule</span>
          </div>
        </div>
        <aside v-if="level === 1" class="side panel">
          <p class="kick">Niveau 1 · 12 lignes</p>
          <h2>Goulots et anomalies</h2>
          <div class="issues">
            <article v-for="issue in graph.diagnostics" :key="issue.id" class="issue">
              <span class="tag" :class="issue.tag_class">{{ issue.tag }}</span>
              <div>
                <b>{{ issue.title }}</b>
                <span class="t">{{ issue.detail }}</span>
                <code class="code">{{ issue.code }}</code>
              </div>
            </article>
          </div>
          <p class="hint">Cliquer une ligne descend au niveau 2.</p>
        </aside>
        <MetroEdgePanel
          v-else-if="level === 2 && selectedEdge"
          :edge="selectedEdge"
          :origin="graph.stations.find((s) => s.id === selectedEdge.origin)"
          :dest="graph.stations.find((s) => s.id === selectedEdge.dest)"
          :detail="body.edge_details[selectedEdge.id]"
        />
        <MetroStationPanel
          v-else-if="level === 2 && selectedStation"
          :station="selectedStation"
          :stats="{
            passes_24h: body.station_stats[selectedStation.id]?.passes_24h ?? 0,
            median_ms: body.station_stats[selectedStation.id]?.median_ms ?? null,
            last_subject_ref: body.station_stats[selectedStation.id]?.last_subject_ref ?? null,
          }"
          :outbound="(body.station_details[selectedStation.id]?.outbound as any) ?? []"
          @enter="enterStation"
        />
        <aside v-else-if="level === 3" class="side panel" data-testid="metro-finalisation-panel">
          <p class="kick">Finalisation · niveau 3</p>
          <h2>Correspondances en sortie</h2>
          <div class="links">
            <div
              v-for="row in (body.station_details.RNG5?.outbound as any) ?? []"
              :key="row.edge_id"
              class="link-row"
              :class="{ lit: row.lit, bad: row.kind === 'bloque' }"
            >
              <span>{{ row.dest_line }} · {{ row.dest_label }}</span>
              <span class="n">{{ row.untraceable ? "?" : row.passes_24h }}</span>
            </div>
          </div>
          <h3>Dernier passage enregistré</h3>
          <p class="hint" v-if="body.station_stats.RNG5?.last_subject_ref">
            {{ body.station_stats.RNG5.last_subject_ref }}
          </p>
          <button type="button" class="button-link" @click="level = 2">Remonter au niveau 2</button>
        </aside>
      </div>

      <section v-else class="metro-mobile stack">
        <p class="lvl-chip">Niveau {{ level }} · stations</p>
        <div class="mini plan">
          <MetroMapSvg
            :graph="graph"
            :level="1"
            :lit-edges="litEdges"
            :selected-station-id="selectedStationId"
            :selected-edge-id="null"
            :dim-others="false"
            :edge-hits="body.edge_hits"
            :level1-edges="level1Edges"
            @select-station="onSelectStation"
            @select-edge="onSelectEdge"
            @select-line="onSelectLine"
          />
        </div>
        <div v-for="line in graph.lines" :key="line.name" class="acc-line">
          <button
            type="button"
            class="acc-h acc-toggle"
            @click="expandedLine = expandedLine === line.name ? null : line.name"
          >
            <span class="pastille" :style="{ background: line.color }" />
            <strong>{{ line.number }} {{ lineDisplayName[line.name] ?? line.name }}</strong>
            <span class="meta">{{ lineSummary(line.name) }}</span>
          </button>
          <div v-if="expandedLine === line.name" class="acc-b">
            <div
              v-for="station in graph.stations.filter((s) => s.line === line.name)"
              :key="station.id"
              class="st-row"
            >
              <span>{{ station.label }}</span>
              <span class="c">
                {{ body.station_stats[station.id]?.passes_24h ?? 0 }} passages
              </span>
            </div>
            <button
              v-if="line.name === 'Rangement'"
              type="button"
              class="button"
              @click="onSelectStation('RNG5')"
            >
              Entrer dans Finalisation — niveau 3
            </button>
          </div>
        </div>
        <p class="note-foot">Lecture seule.</p>
      </section>
    </template>
    <p v-else-if="loading" class="hint">Chargement de l'activité réseau…</p>
  </div>
</template>