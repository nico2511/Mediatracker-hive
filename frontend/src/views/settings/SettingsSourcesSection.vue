<script setup lang="ts">
import { computed, onMounted, reactive, ref } from "vue";
import { RouterLink } from "vue-router";

import CometInstanceList from "../../components/CometInstanceList.vue";
import HosterOrderList from "../../components/HosterOrderList.vue";
import StremioAddonList, { type StremioAddonRow } from "../../components/StremioAddonList.vue";
import SourceRank from "../../components/SourceRank.vue";
import { api, userFacingApiError } from "../../api";
import { fetchSettings, patchSettings } from "../../settings";
import { settingAsCommaList, settingAsLineList, settingAsString, settingCommaTokens } from "../../settingsCoerce";
import { sourceDisplayLabel } from "../../sourceLabels";
import { formatParisClock } from "../../time";

type SourceStatus = {
  source: string;
  label: string;
  enabled: boolean;
  available: boolean;
  rank: number;
  state: string | null;
  message: string;
  since: string | null;
  last_probe_at: string | null;
  last_probe_link_count: number | null;
  last_probe_duration_ms: number | null;
  last_probe_issue: string | null;
  last_probe_reason: string | null;
  last_success_at: string | null;
};

const SOURCE_IDS = [
  "wawacity",
  "free_telecharger",
  "hive",
  "comet",
  "torrentio",
  "stremio_addons",
] as const;

const sourceOrder = ref("wawacity,free_telecharger,hive,comet,torrentio,stremio_addons");
const statuses = ref<SourceStatus[]>([]);
const availableSourceIds = ref<string[]>([]);
const downloadReady = ref(true);
const blockedMessage = ref("");
const expanded = reactive<Record<string, boolean>>({});

const requestDelay = ref(5);
const cacheSeconds = ref(900);
const errorBackoffMin = ref(180);
const remoteSearchCooldownHours = ref(6);
const hosterOrder = ref<string[]>(["rapidgator", "turbobit", "uploady", "dailyuploads", "1fichier"]);

const wawacityUrl = ref("");
const wawacityAgent = ref("");
const freeTelechargerUrl = ref("");
const freeTelechargerAgent = ref("");
const torrentioUrl = ref("");
const cometInstanceUrls = ref<string[]>([]);
const stremioAddonRows = ref<StremioAddonRow[]>([]);

const wawacityDomain = ref<{ current: string | null; candidate: string | null; previous: string | null } | null>(
  null,
);

const sourceProviders = ref<{ id: number; type: string }[]>([]);

const probeQuery = ref("");
const probeTmdb = ref("");
const probeBusy = ref(false);
const probeError = ref("");
const probeResults = ref<
  { source: string; label: string; issue: string; link_count: number; reason: string | null; duration_ms: number }[]
>([]);
const probeFinishedAt = ref<string | null>(null);
const probeProfileName = ref<string | null>(null);

const hiveRucheCount = ref<number | null>(null);

const message = ref("");
const error = ref("");
const saving = ref(false);

const statusBySource = computed(() => new Map((statuses.value ?? []).map((row) => [row.source, row])));

const displaySourceIds = computed(() => availableSourceIds.value);

const orderedSources = computed(() => {
  const visible = new Set(displaySourceIds.value);
  const enabled = settingCommaTokens(sourceOrder.value).filter((id) => visible.has(id));
  const disabledTail = SOURCE_IDS.filter((id) => !enabled.includes(id) && visible.has(id));
  return [...enabled, ...disabledTail];
});

function labelForSource(id: string): string {
  const row = statusBySource.value.get(id);
  return sourceDisplayLabel(id, row?.label);
}

function cardRank(id: string): string {
  if (!isEnabled(id)) {
    return "—";
  }
  const rank = statusBySource.value.get(id)?.rank;
  return rank != null && rank > 0 ? String(rank) : "—";
}

function parseEnabled(): string[] {
  return settingCommaTokens(sourceOrder.value);
}

function isEnabled(id: string): boolean {
  return parseEnabled().includes(id);
}

function setEnabled(id: string, on: boolean) {
  const enabled = parseEnabled();
  if (on) {
    if (!enabled.includes(id)) {
      sourceOrder.value = [...enabled, id].join(",");
    }
    return;
  }
  const next = enabled.filter((item) => item !== id);
  if (!next.length) {
    return;
  }
  sourceOrder.value = next.join(",");
}

function statusDotClass(source: string): string {
  const row = statusBySource.value.get(source);
  if (!row?.state) {
    return "source-dot unknown";
  }
  return row.state === "ok" ? "source-dot ok" : "source-dot down";
}

function statusLine(source: string): string {
  if (!isEnabled(source)) {
    return "Désactivée";
  }
  const row = statusBySource.value.get(source);
  if (!row) {
    return "";
  }
  if (source === "hive" && hiveRucheCount.value != null) {
    const n = hiveRucheCount.value;
    return n === 1 ? "1 ruche" : `${n} ruches`;
  }
  if (row.state === "down") {
    const since = row.since ? formatParisClock(row.since) : "";
    return since ? `En panne depuis ${since}` : "En panne";
  }
  if (row.state === "ok" && row.since) {
    return `En ligne depuis ${formatParisClock(row.since)}`;
  }
  return row.message || "";
}

function lastProbeOutcomeText(row: SourceStatus): string {
  const issue = (row.last_probe_issue || "").trim();
  const reason = (row.last_probe_reason || "").trim();
  if (issue === "found") {
    const n = row.last_probe_link_count ?? 0;
    if (n > 0) {
      return `trouvé ${n} lien${n > 1 ? "s" : ""}`;
    }
    return "trouvé";
  }
  if (issue === "empty") {
    return "aucun résultat";
  }
  if (issue === "error") {
    return reason ? `erreur : ${reason}` : "erreur";
  }
  if (issue === "skipped") {
    return reason ? `ignorée : ${reason}` : "ignorée";
  }
  if (issue === "refused") {
    return reason ? `refusée : ${reason}` : "refusée";
  }
  if (reason) {
    return reason;
  }
  return "";
}

function lastProbeLine(source: string): string {
  const row = statusBySource.value.get(source);
  if (!row?.last_probe_at) {
    return "";
  }
  const clock = formatParisClock(row.last_probe_at);
  const parts = [`Dernier test ${clock}`];
  const outcome = lastProbeOutcomeText(row);
  if (outcome) {
    parts.push(outcome);
  }
  if (row.last_probe_duration_ms != null) {
    parts.push(`${row.last_probe_duration_ms} ms`);
  }
  return parts.join(" · ");
}

function lastSuccessLine(source: string): string {
  const row = statusBySource.value.get(source);
  if (!row?.last_success_at) {
    return "";
  }
  return `Dernier succès : ${formatParisClock(row.last_success_at)}`;
}

async function loadStatuses() {
  const body = await api<{
    sources?: SourceStatus[] | null;
    available_sources?: string[];
    download_ready?: boolean;
    blocked_message?: string | null;
  }>("/api/v1/settings/sources/status");
  statuses.value = Array.isArray(body?.sources) ? body.sources : [];
  availableSourceIds.value = Array.isArray(body?.available_sources) ? body.available_sources : [];
  downloadReady.value = body?.download_ready !== false;
  blockedMessage.value = (body?.blocked_message || "").trim();
}

async function loadHiveRuches() {
  try {
    const rows = await api<{ ruche_id_hex: string }[] | null | undefined>("/api/v1/hive/ruches");
    hiveRucheCount.value = Array.isArray(rows) ? rows.length : null;
  } catch {
    hiveRucheCount.value = null;
  }
}

async function loadWawacityDomain() {
  try {
    wawacityDomain.value = await api("/api/v1/wawacity/domain");
  } catch {
    wawacityDomain.value = null;
  }
}

async function loadProviders() {
  const providers = await api<{ items?: { id: number; type: string; config: Record<string, unknown> }[] | null }>(
    "/api/v1/providers?kind=source",
  );
  const items = Array.isArray(providers?.items) ? providers.items : [];
  sourceProviders.value = items.map((item) => ({ id: item.id, type: item.type }));
  for (const item of items) {
    const url = item.config?.base_url;
    if (typeof url !== "string") {
      continue;
    }
    if (item.type === "wawacity") {
      wawacityUrl.value = url;
    }
    if (item.type === "free_telecharger") {
      freeTelechargerUrl.value = url;
    }
    if (item.type === "torrentio") {
      torrentioUrl.value = url;
    }
  }
}

async function loadSettings() {
  const body = await fetchSettings();
  for (const row of body.settings) {
    if (row.key === "download_source_order") {
      const next = settingAsCommaList(row.value);
      if (next) {
        sourceOrder.value = next;
      }
    }
    if (row.key === "source_request_delay_seconds" && typeof row.value === "number") {
      requestDelay.value = row.value;
    }
    if (row.key === "source_cache_seconds" && typeof row.value === "number") {
      cacheSeconds.value = row.value;
    }
    if (row.key === "source_error_backoff_min" && typeof row.value === "number") {
      errorBackoffMin.value = row.value;
    }
    if (row.key === "remote_search_cooldown_hours" && typeof row.value === "number") {
      remoteSearchCooldownHours.value = row.value;
    }
    if (row.key === "hoster_preference") {
      const tokens = settingCommaTokens(settingAsCommaList(row.value));
      if (tokens.length) {
        hosterOrder.value = tokens;
      }
    }
    if (row.key === "wawacity_base_url") {
      const next = settingAsString(row.value).trim();
      if (next) {
        wawacityUrl.value = next;
      }
    }
    if (row.key === "wawacity_user_agent") {
      wawacityAgent.value = settingAsString(row.value);
    }
    if (row.key === "free_telecharger_user_agent") {
      freeTelechargerAgent.value = settingAsString(row.value);
    }
    if (row.key === "torrentio_base_url") {
      const next = settingAsString(row.value).trim();
      if (next) {
        torrentioUrl.value = next;
      }
    }
    if (row.key === "comet_base_urls") {
      const lines = settingAsLineList(row.value);
      if (lines.length) {
        cometInstanceUrls.value = lines;
      }
    }
    if (row.key === "comet_base_url" && !cometInstanceUrls.value.length) {
      const legacy = settingAsString(row.value).trim();
      if (legacy) {
        cometInstanceUrls.value = [legacy];
      }
    }
    if (row.key === "stremio_addons") {
      const raw = row.value;
      if (Array.isArray(raw)) {
        stremioAddonRows.value = raw
          .filter((item): item is Record<string, unknown> => item && typeof item === "object")
          .map((item) => ({
            url: String(item.url ?? "").trim(),
            enabled: item.enabled !== false && item.enabled !== "false" && item.enabled !== "0",
          }))
          .filter((item) => item.url);
      } else if (typeof raw === "string" && raw.trim()) {
        try {
          const parsed = JSON.parse(raw) as unknown;
          if (Array.isArray(parsed)) {
            stremioAddonRows.value = parsed
              .filter((item): item is Record<string, unknown> => item && typeof item === "object")
              .map((item) => ({
                url: String(item.url ?? "").trim(),
                enabled: item.enabled !== false,
              }))
              .filter((item) => item.url);
          }
        } catch {
          /* invalid JSON left empty */
        }
      }
    }
  }
}

async function refresh() {
  await Promise.all([loadStatuses(), loadHiveRuches(), loadWawacityDomain()]);
}

onMounted(async () => {
  try {
    await loadSettings();
    await loadProviders();
    await refresh();
  } catch (err) {
    error.value = userFacingApiError(err, "Chargement des sources impossible.");
  }
});

async function saveCore() {
  message.value = "";
  error.value = "";
  saving.value = true;
  try {
    await patchSettings({
      values: {
        download_source_order: sourceOrder.value,
        source_request_delay_seconds: requestDelay.value,
        source_cache_seconds: cacheSeconds.value,
        source_error_backoff_min: errorBackoffMin.value,
        remote_search_cooldown_hours: remoteSearchCooldownHours.value,
        hoster_preference: hosterOrder.value.join(","),
        wawacity_base_url: wawacityUrl.value,
        wawacity_user_agent: wawacityAgent.value,
        free_telecharger_user_agent: freeTelechargerAgent.value,
        torrentio_base_url: torrentioUrl.value,
        comet_base_urls: cometInstanceUrls.value.join("\n"),
        comet_base_url: cometInstanceUrls.value[0] ?? "",
        stremio_addons: JSON.stringify(stremioAddonRows.value),
      },
    });
    await saveProviderUrls();
    message.value = "Enregistré.";
    await refresh();
  } catch (err) {
    error.value = userFacingApiError(err, "Enregistrement impossible.");
  } finally {
    saving.value = false;
  }
}

async function saveProviderUrls() {
  for (const provider of sourceProviders.value) {
    if (provider.type === "wawacity") {
      await api(`/api/v1/providers/${provider.id}`, {
        method: "PATCH",
        body: JSON.stringify({ config: { base_url: wawacityUrl.value } }),
      });
    }
    if (provider.type === "free_telecharger") {
      await api(`/api/v1/providers/${provider.id}`, {
        method: "PATCH",
        body: JSON.stringify({ config: { base_url: freeTelechargerUrl.value } }),
      });
    }
    if (provider.type === "torrentio") {
      await api(`/api/v1/providers/${provider.id}`, {
        method: "PATCH",
        body: JSON.stringify({ config: { base_url: torrentioUrl.value } }),
      });
    }
    if (provider.type === "comet") {
      await api(`/api/v1/providers/${provider.id}`, {
        method: "PATCH",
        body: JSON.stringify({
          config: {
            base_url: cometInstanceUrls.value[0] ?? "",
            base_urls: [...cometInstanceUrls.value],
          },
        }),
      });
    }
    if (provider.type === "stremio_addons") {
      await api(`/api/v1/providers/${provider.id}`, {
        method: "PATCH",
        body: JSON.stringify({
          config: {
            base_url: stremioAddonRows.value[0]?.url ?? "",
            addons: stremioAddonRows.value.map((row) => ({
              url: row.url,
              enabled: row.enabled,
            })),
          },
        }),
      });
    }
  }
}

async function revertWawacityDomain() {
  try {
    wawacityDomain.value = await api("/api/v1/wawacity/domain/revert", { method: "POST" });
    await loadSettings();
    await loadProviders();
    message.value = "Domaine Wawacity rétabli.";
  } catch (err) {
    error.value = userFacingApiError(err, "Retour au domaine précédent impossible.");
  }
}

type ProbeResultRow = {
  source: string;
  label: string;
  issue: string;
  link_count: number;
  reason: string | null;
  duration_ms: number;
};

async function probeOneSource(query: string, sourceId: string): Promise<ProbeResultRow[]> {
  const body = await api<{ results: ProbeResultRow[]; profile_name?: string | null }>("/api/v1/settings/sources/probe", {
    method: "POST",
    body: JSON.stringify({
      query,
      tmdb_id: probeTmdb.value.trim() ? Number(probeTmdb.value.trim()) : null,
      sources: [sourceId],
    }),
  });
  if (body.profile_name) {
    probeProfileName.value = body.profile_name;
  }
  if (sourceId === "stremio_addons") {
    return body.results ?? [];
  }
  const row = body.results[0];
  return row ? [row] : [];
}

async function runProbe(sources?: string[]) {
  probeError.value = "";
  probeResults.value = [];
  probeProfileName.value = null;
  const query = probeQuery.value.trim();
  if (!query) {
    probeError.value = "Indiquez un titre (film ou épisode S01E01).";
    return;
  }
  const targets = sources?.length ? sources : [...orderedSources.value];
  probeBusy.value = true;
  try {
    for (const sourceId of targets) {
      try {
        const rows = await probeOneSource(query, sourceId);
        if (rows.length) {
          probeResults.value = [...probeResults.value, ...rows];
        }
      } catch (err) {
        const label = labelForSource(sourceId);
        probeResults.value = [
          ...probeResults.value,
          {
            source: sourceId,
            label,
            issue: "error",
            link_count: 0,
            reason: userFacingApiError(err, "Test impossible."),
            duration_ms: 0,
          },
        ];
      }
    }
    probeFinishedAt.value = new Date().toISOString();
    await loadStatuses();
  } finally {
    probeBusy.value = false;
  }
}

function toggleExpanded(id: string) {
  expanded[id] = !expanded[id];
}

function probeRowDetail(row: ProbeResultRow): string {
  let base = "";
  if (row.reason) {
    base = row.reason;
  } else if (row.link_count) {
    base = `${row.link_count} lien${row.link_count > 1 ? "s" : ""}`;
  } else if (row.issue === "empty") {
    base = "aucune source";
  } else {
    base = row.issue;
  }
  const profile = probeProfileName.value;
  if (profile && base && !base.toLowerCase().includes("profil")) {
    return `${base} — profil « ${profile} »`;
  }
  return base;
}

function formatProbeMs(ms: number): string {
  if (ms >= 1000) {
    return `${(ms / 1000).toFixed(1).replace(".", ",")} s`;
  }
  return `${ms} ms`;
}

const SOURCE_BLURBS: Record<string, string> = {
  wawacity: "Site de liens — hébergeurs via AllDebrid",
  free_telecharger: "Site de liens — hébergeurs via AllDebrid",
  hive: "Pair à pair entre tes instances",
  comet: "Cache AllDebrid — instances publiques sans clé",
  torrentio: "Addon public sans clé — aimant envoyé à AllDebrid",
  stremio_addons:
    "Plusieurs addons Stremio — torrents ou hébergeurs (profil qualité, sans clé vers les addons)",
};
</script>

<template>
  <div class="settings-sources settings-section stack">
    <header class="sources-head">
      <h2 class="settings-section-title">Sources de téléchargement</h2>
      <p class="hint">
        Ordre, tests et réglages par source. Une fiche peut avoir son propre ordre (fiche Médias &gt; Source).
      </p>
    </header>

    <p v-if="!downloadReady && blockedMessage" class="hint source-blocked-banner" role="status">
      {{ blockedMessage }}
      <RouterLink to="/reglages/alldebrid">AllDebrid</RouterLink>
      ou
      <RouterLink to="/reglages/hive">Hive</RouterLink>.
    </p>

    <section class="panel stack source-order-panel">
      <h3>Ordre des sources</h3>
      <p class="hint">
        Les sources sont essayées dans cet ordre. Une source désactivée reste visible, en bas de liste.
      </p>
      <SourceRank v-model="sourceOrder" order-only :visible-ids="displaySourceIds" />
      <button type="button" class="btn" :disabled="saving" @click="saveCore">Enregistrer l’ordre</button>
    </section>

    <section class="panel stack source-probe-panel">
      <h3>Tester les sources</h3>
      <label>
        <span>Titre</span>
        <input v-model="probeQuery" placeholder="Titre fictif ou Titre fictif S01E02" />
      </label>
      <label>
        <span>TMDB (optionnel)</span>
        <input v-model="probeTmdb" inputmode="numeric" placeholder="900101" />
      </label>
      <button type="button" class="btn primary" :disabled="probeBusy" @click="runProbe()">
        {{ probeBusy ? "Test en cours…" : "Tester toutes" }}
      </button>
      <ul v-if="probeResults.length" class="source-probe-results">
        <li v-for="row in probeResults" :key="row.source + ':' + row.label">
          <span :class="statusDotClass(row.source.split(':')[0])" aria-hidden="true"></span>
          <span class="source-probe-name">{{ row.label }}</span>
          <span class="source-probe-detail">{{ probeRowDetail(row) }}</span>
          <span class="source-probe-ms">{{ formatProbeMs(row.duration_ms) }}</span>
        </li>
      </ul>
      <p v-if="probeProfileName" class="hint">Profil utilisé : {{ probeProfileName }}</p>
      <p v-if="probeFinishedAt" class="hint">
        Test fait à {{ formatParisClock(probeFinishedAt) }} — aucun téléchargement lancé.
      </p>
      <p v-if="probeError" class="error">{{ probeError }}</p>
    </section>

    <section class="panel stack source-rhythm-panel">
      <h3>Rythme des requêtes (toutes les sources)</h3>
      <p class="hint">
        {{ requestDelay }} s entre requêtes — {{ Math.round(cacheSeconds / 60) }} min de cache — {{ errorBackoffMin }}
        min de recul après erreur.
      </p>
      <label>
        <span>Délai entre requêtes (secondes)</span>
        <input v-model.number="requestDelay" type="number" min="2" max="60" />
      </label>
      <label>
        <span>Cache des pages (secondes)</span>
        <input v-model.number="cacheSeconds" type="number" min="0" max="3600" />
      </label>
      <label>
        <span>Recul après erreur (minutes)</span>
        <input v-model.number="errorBackoffMin" type="number" min="1" max="1440" />
      </label>
      <label>
        <span>Délai entre deux recherches distantes d'une même fiche (heures)</span>
        <input v-model.number="remoteSearchCooldownHours" type="number" min="1" max="72" />
      </label>
      <HosterOrderList v-model="hosterOrder" />
      <button type="button" class="btn" :disabled="saving" @click="saveCore">Enregistrer le rythme</button>
    </section>

    <article
      v-for="id in orderedSources"
      :key="id"
      class="panel source-card"
      :class="{ expanded: expanded[id], 'is-off': !isEnabled(id) }"
    >
      <header class="source-card-head">
        <span class="source-card-rank">{{ cardRank(id) }}</span>
        <div class="source-card-titles">
          <strong>{{ labelForSource(id) }}</strong>
          <span class="hint">{{ SOURCE_BLURBS[id] }}</span>
        </div>
        <div class="source-card-status" :class="{ 'is-disabled-status': !isEnabled(id) }">
          <span v-if="isEnabled(id)" :class="statusDotClass(id)" aria-hidden="true"></span>
          <span>{{ statusLine(id) }}</span>
        </div>
        <label class="source-enabled switch-line">
          <span>{{ isEnabled(id) ? "Activée" : "Désactivée" }}</span>
          <span class="switch">
            <input type="checkbox" :checked="isEnabled(id)" @change="setEnabled(id, ($event.target as HTMLInputElement).checked); saveCore()" />
            <span class="switch-ui" aria-hidden="true"></span>
          </span>
        </label>
      </header>
      <p v-if="lastProbeLine(id)" class="hint source-card-meta">{{ lastProbeLine(id) }}</p>
      <p v-if="lastSuccessLine(id)" class="hint source-card-meta">{{ lastSuccessLine(id) }}</p>
      <p v-if="statusBySource.get(id)?.state === 'down' && statusBySource.get(id)?.message" class="error">
        {{ statusBySource.get(id)?.message }}
      </p>
      <div class="source-card-actions">
        <button type="button" class="btn btn-sm" :disabled="probeBusy" @click="runProbe([id])">Tester</button>
        <button type="button" class="btn btn-sm" @click="toggleExpanded(id)">
          {{ expanded[id] ? "Masquer ▾" : "Réglages ▾" }}
        </button>
      </div>

      <div v-if="expanded[id]" class="source-card-body stack">
        <template v-if="id === 'wawacity'">
          <div
            v-if="wawacityDomain?.candidate && statusBySource.get('wawacity')?.available"
            class="source-info banner"
          >
            Domaine changé automatiquement.
            <button v-if="wawacityDomain?.previous" type="button" class="btn btn-sm" @click="revertWawacityDomain">
              Revenir à l’ancien
            </button>
          </div>
          <label>
            <span>Adresse du site</span>
            <input v-model="wawacityUrl" placeholder="https://domaine-actuel.example" />
          </label>
          <label>
            <span>User-Agent</span>
            <input v-model="wawacityAgent" placeholder="Navigateur, vide = valeur par défaut" />
          </label>
        </template>

        <template v-else-if="id === 'free_telecharger'">
          <label>
            <span>Adresse du site</span>
            <input v-model="freeTelechargerUrl" placeholder="https://catalogue-neutre.example" />
          </label>
          <label>
            <span>User-Agent</span>
            <input v-model="freeTelechargerAgent" placeholder="Navigateur, vide = valeur par défaut" />
          </label>
        </template>

        <template v-else-if="id === 'hive'">
          <p class="hint">Hive se règle dans sa propre page : ruches et partage.</p>
          <RouterLink class="btn" :to="{ name: 'settings-hive' }">Ouvrir Réglages Hive</RouterLink>
        </template>

        <template v-else-if="id === 'comet'">
          <p class="hint">Clé AllDebrid jamais envoyée aux instances publiques.</p>
          <CometInstanceList v-model="cometInstanceUrls" />
        </template>

        <template v-else-if="id === 'torrentio'">
          <label>
            <span>Adresse de l’addon</span>
            <input v-model="torrentioUrl" placeholder="https://torrentio.strem.fun" />
          </label>
          <p class="hint">Sans clé : MediaTracker envoie lui-même l’aimant à AllDebrid.</p>
        </template>

        <template v-else-if="id === 'stremio_addons'">
          <p class="hint">Aucune URL réelle par défaut. Les clés collées dans l’URL sont retirées avant enregistrement.</p>
          <StremioAddonList v-model="stremioAddonRows" />
        </template>

        <button type="button" class="btn" :disabled="saving" @click="saveCore">Enregistrer</button>
      </div>
    </article>

    <p v-if="message" class="hint">{{ message }}</p>
    <p v-if="error" class="error">{{ error }}</p>
  </div>
</template>
