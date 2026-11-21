<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue";
import { RouterLink, useRoute } from "vue-router";

import { api, userFacingApiError } from "../../api";
import HiveCatalogResetButton from "../../components/hive/HiveCatalogResetButton.vue";
import HivePendingJoinList, { type PendingJoinRow } from "../../components/hive/HivePendingJoinList.vue";
import { formatParisClock, parseInstant } from "../../time";

const MEMBERS_PAGE_SIZE = 20;

type QuotaStatus = "normal" | "near" | "exceeded";

interface MemberRow {
  instance_id: string;
  display_name: string;
  pull_only: boolean;
  is_self: boolean;
  app_version: string | null;
  hive_protocol: number;
  capabilities: string[];
  shares_in_ruche?: boolean;
  receive_bytes_used?: number | null;
  receive_quota_bytes?: number | null;
  receive_quota_status?: QuotaStatus | null;
}

function versionParts(raw: string | null | undefined): { display: string; title: string } {
  if (!raw) {
    return { display: "", title: "" };
  }
  const at = raw.indexOf("@");
  if (at < 0) {
    return { display: raw, title: raw };
  }
  return { display: raw.slice(0, at), title: raw };
}

function versionPillLabel(
  m: MemberRow,
  localProtocol: number,
): { text: string; title: string; tone: "muted" | "ok" | "warn" } {
  if (!m.app_version) {
    return { text: "version inconnue", title: "", tone: "muted" };
  }
  const { display, title } = versionParts(m.app_version);
  const upToDate = m.hive_protocol >= localProtocol;
  const text = upToDate ? `v${display} · à jour` : `v${display} · ancienne version`;
  return { text, title, tone: upToDate ? "ok" : "warn" };
}

interface RucheDetail {
  ruche_id_hex: string;
  is_creator: boolean;
  weekly_quota_bytes: number;
  quota_week_start?: string;
  quota_reset_at?: string;
  catalog_epoch: number;
  catalog_reset_at: string | null;
  members: MemberRow[];
}

type MyQuotaSourceStatus = "ok" | "unavailable" | "no_quota";

interface MyQuotaSourceRow {
  status: MyQuotaSourceStatus;
  source_instance_id: string;
  source_display_name: string;
  shares_in_ruche?: boolean | null;
  bytes_used?: number | null;
  quota_bytes?: number | null;
  week_start?: string | null;
  reset_at?: string | null;
  receive_quota_status?: QuotaStatus | null;
}

interface MyQuotaDetail {
  ruche_id_hex: string;
  week_start: string;
  reset_at: string;
  sources: MyQuotaSourceRow[];
}

function formatGoFr(bytes: number): string {
  const gb = bytes / 1024 ** 3;
  return `${gb.toLocaleString("fr-FR", { minimumFractionDigits: 1, maximumFractionDigits: 1 })} Go`;
}

function quotaResetLabel(iso: string | undefined, exceeded: boolean): string {
  const parsed = parseInstant(iso);
  if (!parsed) {
    return "";
  }
  const wd = parsed.toLocaleDateString("fr-FR", { timeZone: "Europe/Paris", weekday: "long" });
  const day = parsed.toLocaleDateString("fr-FR", {
    timeZone: "Europe/Paris",
    day: "numeric",
    month: "short",
  });
  const clock = formatParisClock(iso);
  const verb = exceeded ? "Reprise" : "Remise à zéro";
  return `${verb} ${wd} ${day} ${clock}, heure de Paris`;
}

function weekHeading(iso: string | undefined): string {
  const parsed = parseInstant(iso);
  if (!parsed) {
    return "";
  }
  const day = parsed.toLocaleDateString("fr-FR", {
    timeZone: "Europe/Paris",
    day: "numeric",
    month: "short",
  });
  return `semaine du ${day}`;
}

function quotaUsagePct(m: MemberRow): number {
  const used = m.receive_bytes_used ?? 0;
  const limit = m.receive_quota_bytes ?? 1;
  return Math.min(100, Math.round((used / limit) * 1000) / 10);
}

function myQuotaUsagePct(row: MyQuotaSourceRow): number {
  const used = row.bytes_used ?? 0;
  const limit = row.quota_bytes ?? 1;
  return Math.min(100, Math.round((used / limit) * 1000) / 10);
}

function quotaStatusLabel(status: QuotaStatus | null | undefined): string {
  if (status === "exceeded") {
    return "Quota atteint";
  }
  if (status === "near") {
    return "Proche du quota";
  }
  return "Normal";
}

const HIVE_PROTOCOL_FALLBACK = 1;

const route = useRoute();
const rucheIdHex = computed(() => String(route.params.rucheIdHex || ""));
const detail = ref<RucheDetail | null>(null);
const myQuota = ref<MyQuotaDetail | null>(null);
const myQuotaLoading = ref(false);
const myQuotaError = ref("");

const selfMember = computed(() => detail.value?.members.find((m) => m.is_self) ?? null);

const localHiveProtocol = computed(
  () => selfMember.value?.hive_protocol ?? HIVE_PROTOCOL_FALLBACK,
);
const pendingJoins = ref<PendingJoinRow[]>([]);
const quotaGb = ref(100);
const memberSearch = ref("");
const memberPage = ref(1);
const error = ref("");
const note = ref("");
const loading = ref(false);
const quotaResetMember = ref<MemberRow | null>(null);
const quotaResetBusy = ref(false);
const catalogResetNotice = computed(() => {
  const raw = detail.value?.catalog_reset_at;
  if (!raw || detail.value?.is_creator) {
    return "";
  }
  const d = new Date(raw);
  if (Number.isNaN(d.getTime())) {
    return "";
  }
  const dd = String(d.getDate()).padStart(2, "0");
  const mm = String(d.getMonth() + 1).padStart(2, "0");
  return `Le maître a réinitialisé le catalogue le ${dd}/${mm}.`;
});

const filteredMembers = computed(() => {
  const q = memberSearch.value.trim().toLowerCase();
  const rows = detail.value?.members ?? [];
  if (!q) {
    return rows;
  }
  return rows.filter(
    (m) =>
      m.display_name.toLowerCase().includes(q) ||
      m.instance_id.toLowerCase().includes(q),
  );
});

const memberPageCount = computed(() =>
  Math.max(1, Math.ceil(filteredMembers.value.length / MEMBERS_PAGE_SIZE)),
);

const pagedMembers = computed(() => {
  const start = (memberPage.value - 1) * MEMBERS_PAGE_SIZE;
  return filteredMembers.value.slice(start, start + MEMBERS_PAGE_SIZE);
});

watch(memberSearch, () => {
  memberPage.value = 1;
});

watch(filteredMembers, () => {
  if (memberPage.value > memberPageCount.value) {
    memberPage.value = memberPageCount.value;
  }
});

async function loadPending() {
  if (!detail.value?.is_creator) {
    pendingJoins.value = [];
    return;
  }
  try {
    pendingJoins.value = await api<PendingJoinRow[]>(`/api/v1/hive/join/pending/${rucheIdHex.value}`);
  } catch (err) {
    error.value = userFacingApiError(err, "Impossible de charger les demandes d’adhésion.");
    pendingJoins.value = [];
  }
}

async function loadMyQuota() {
  if (detail.value?.is_creator) {
    myQuota.value = null;
    return;
  }
  myQuotaLoading.value = true;
  myQuotaError.value = "";
  try {
    myQuota.value = await api<MyQuotaDetail>(`/api/v1/hive/ruches/${rucheIdHex.value}/my-quota`);
  } catch (err) {
    myQuota.value = null;
    myQuotaError.value = userFacingApiError(err, "Quota indisponible.");
  } finally {
    myQuotaLoading.value = false;
  }
}

async function loadDetail() {
  loading.value = true;
  error.value = "";
  try {
    detail.value = await api<RucheDetail>(`/api/v1/hive/ruches/${rucheIdHex.value}/detail`);
    quotaGb.value = Math.round((detail.value.weekly_quota_bytes / 1024 ** 3) * 10) / 10;
    await loadPending();
    if (!detail.value.is_creator) {
      void loadMyQuota();
    } else {
      myQuota.value = null;
      myQuotaError.value = "";
    }
  } catch (err) {
    detail.value = null;
    myQuota.value = null;
    error.value = userFacingApiError(err, "Détail de ruche indisponible.");
  } finally {
    loading.value = false;
  }
}

onMounted(loadDetail);
watch(rucheIdHex, loadDetail);

async function refreshAll() {
  await loadDetail();
}

async function saveQuota() {
  const bytes = Math.max(1, Math.round(quotaGb.value * 1024 ** 3));
  try {
    await api(`/api/v1/hive/ruches/${rucheIdHex.value}/quota`, {
      method: "PATCH",
      body: JSON.stringify({ weekly_quota_bytes: bytes }),
    });
    note.value = "Quota enregistré.";
    await loadDetail();
  } catch (err) {
    error.value = userFacingApiError(err, "Quota impossible.");
  }
}

async function excludeMember(instanceId: string) {
  try {
    await api(`/api/v1/hive/ruches/${rucheIdHex.value}/exclude`, {
      method: "POST",
      body: JSON.stringify({ instance_id: instanceId }),
    });
    note.value = "Membre exclu.";
    await loadDetail();
  } catch (err) {
    error.value = userFacingApiError(err, "Exclusion impossible.");
  }
}

function onPendingError(message: string) {
  error.value = message;
}

function onPendingNote(message: string) {
  note.value = message;
}

function onCatalogResetSuccess() {
  error.value = "";
  note.value = "Catalogue de la ruche réinitialisé.";
  void loadDetail();
}

function onCatalogResetError(message: string) {
  note.value = "";
  error.value = message;
}

const quotaResetLimitGo = computed(() =>
  detail.value ? formatGoFr(detail.value.weekly_quota_bytes) : "",
);

const quotaResetBody = computed(() => {
  const m = quotaResetMember.value;
  const d = detail.value;
  if (!m || !d) {
    return "";
  }
  const used = formatGoFr(m.receive_bytes_used ?? 0);
  const limit = quotaResetLimitGo.value;
  return `Le compteur de ${m.display_name} sur cette instance passera de ${used} à 0 Go pour la semaine en cours. ${m.display_name} pourra à nouveau recevoir jusqu'à ${limit} depuis cette instance.

Le plafond de la ruche (${limit}) ne change pas. Les compteurs tenus par d'autres sources ne sont pas touchés.`;
});

function openQuotaReset(member: MemberRow) {
  quotaResetMember.value = member;
}

function closeQuotaResetDialog() {
  if (quotaResetBusy.value) {
    return;
  }
  quotaResetMember.value = null;
}

async function submitQuotaReset() {
  const member = quotaResetMember.value;
  if (!member) {
    return;
  }
  quotaResetBusy.value = true;
  error.value = "";
  try {
    const pathId = encodeURIComponent(member.instance_id);
    await api(`/api/v1/hive/ruches/${rucheIdHex.value}/members/${pathId}/quota/reset`, {
      method: "POST",
    });
    quotaResetMember.value = null;
    note.value = "Quota remis à zéro.";
    await loadDetail();
  } catch (err) {
    error.value = userFacingApiError(err, "Remise à zéro impossible.");
  } finally {
    quotaResetBusy.value = false;
  }
}
</script>

<template>
  <div class="hive-ruche-members zone stack">
    <header class="row sp hive-ruche-members-head">
      <div class="stack tight">
        <RouterLink class="hint" to="/reglages/hive">← Réglages Hive</RouterLink>
        <h2 class="settings-section-title">
          Ruche {{ rucheIdHex.slice(0, 8) }}… · Membres
        </h2>
      </div>
    </header>

    <p v-if="error" class="error" role="alert">{{ error }}</p>
    <p v-if="note" class="ok">{{ note }}</p>
    <p v-if="loading && !detail" class="hint">Chargement…</p>

    <p
      v-if="catalogResetNotice"
      class="hint hive-catalog-reset-notice"
      data-testid="hive-catalog-reset-notice"
    >
      {{ catalogResetNotice }}
    </p>

    <template v-if="detail">
      <section
        v-if="detail.is_creator"
        class="hive-block panel stack"
        aria-labelledby="hive-members-pending-title"
      >
        <HivePendingJoinList
          :ruche-id-hex="rucheIdHex"
          :rows="pendingJoins"
          @changed="refreshAll"
          @error="onPendingError"
          @note="onPendingNote"
        />
      </section>

      <div class="hive-members-layout">
        <section class="hive-block panel stack" aria-labelledby="hive-members-title">
          <div class="row sp hive-members-toolbar">
            <h3 id="hive-members-title" class="settings-subsection-title">Membres</h3>
            <label class="hive-field hive-members-search">
              <span class="label">Rechercher</span>
              <input
                v-model="memberSearch"
                class="input"
                type="search"
                placeholder="Nom ou identifiant"
                data-testid="hive-members-search"
              />
            </label>
          </div>

          <p
            v-if="detail.is_creator && detail.quota_week_start"
            class="hint hive-members-week"
            data-testid="hive-members-week"
          >
            Consommation reçue depuis cette instance,
            {{ weekHeading(detail.quota_week_start) }}.
          </p>

          <p
            v-if="!detail.is_creator"
            class="hint hive-members-non-creator"
            data-testid="hive-members-non-creator-hint"
          >
            La consommation des autres membres n'est pas affichée ici. Les options de plafond et de remise à
            zéro sont réservées au propriétaire de la ruche.
          </p>

          <p
            v-if="selfMember?.app_version"
            class="hive-self-version hint"
            data-testid="hive-self-version-announce"
          >
            Cette instance annonce v{{ versionParts(selfMember.app_version).display }}
          </p>

          <ul class="hive-member-list" data-testid="hive-member-list">
            <li
              v-for="m in pagedMembers"
              :key="m.instance_id"
              class="hive-member-row stack"
              data-testid="hive-member-row"
            >
              <div class="row sp hive-member-main">
                <div class="hive-member-identity stack tight">
                  <span class="hive-member-name">
                    {{ m.display_name }}
                    <span v-if="m.is_self" class="hint">(cette instance)</span>
                    <span v-else-if="m.pull_only" class="hint">(tirage)</span>
                  </span>
                  <span
                    class="hive-version-pill"
                    :class="`hive-version-pill--${versionPillLabel(m, localHiveProtocol).tone}`"
                    :title="versionPillLabel(m, localHiveProtocol).title || undefined"
                    data-testid="hive-member-version-pill"
                  >
                    {{ versionPillLabel(m, localHiveProtocol).text }}
                  </span>
                </div>
                <button
                  v-if="detail.is_creator && !m.is_self"
                  type="button"
                  class="btn btn-sm danger"
                  data-testid="hive-exclude-member"
                  @click="excludeMember(m.instance_id)"
                >
                  Exclure
                </button>
              </div>

              <p v-if="m.shares_in_ruche" class="hint hive-member-share" data-testid="hive-member-shares">
                Partage dans la ruche · pas de quota
              </p>
              <template v-else-if="!detail.is_creator">
                <p v-if="m.is_self" class="hint hive-member-quota-remote" data-testid="hive-member-quota-remote">
                  Ne partage rien · quota applicable chez les sources
                </p>
              </template>
              <template v-else-if="m.receive_quota_bytes != null">
                <p class="hint hive-member-quota-intro">Ne partage rien · quota applicable</p>
                <div class="hive-quota-line row sp" data-testid="hive-member-quota-line">
                  <span>
                    Reçu cette semaine
                    {{ formatGoFr(m.receive_bytes_used ?? 0) }} /
                    {{ formatGoFr(m.receive_quota_bytes) }}
                  </span>
                  <span
                    class="hive-quota-status-pill"
                    :class="`hive-quota-status-pill--${m.receive_quota_status || 'normal'}`"
                    data-testid="hive-member-quota-status"
                  >
                    {{ quotaStatusLabel(m.receive_quota_status) }}
                  </span>
                </div>
                <div
                  class="hive-quota-bar"
                  role="progressbar"
                  :aria-valuenow="quotaUsagePct(m)"
                  aria-valuemin="0"
                  aria-valuemax="100"
                  data-testid="hive-member-quota-bar"
                >
                  <i
                    class="hive-quota-bar-fill"
                    :class="`hive-quota-bar-fill--${m.receive_quota_status || 'normal'}`"
                    :style="{ width: `${quotaUsagePct(m)}%` }"
                  />
                </div>
                <p class="hint hive-member-reset" data-testid="hive-member-reset">
                  {{
                    quotaResetLabel(
                      detail.quota_reset_at,
                      m.receive_quota_status === "exceeded",
                    )
                  }}
                </p>
                <button
                  type="button"
                  class="btn btn-sm"
                  data-testid="hive-member-quota-reset-open"
                  @click="openQuotaReset(m)"
                >
                  Remettre à zéro
                </button>
              </template>
            </li>
          </ul>

          <div
            v-if="filteredMembers.length > MEMBERS_PAGE_SIZE"
            class="row sp hive-members-pagination"
            data-testid="hive-members-pagination"
          >
            <button
              type="button"
              class="btn btn-sm"
              :disabled="memberPage <= 1"
              @click="memberPage -= 1"
            >
              Précédent
            </button>
            <span class="hint">Page {{ memberPage }} / {{ memberPageCount }}</span>
            <button
              type="button"
              class="btn btn-sm"
              :disabled="memberPage >= memberPageCount"
              @click="memberPage += 1"
            >
              Suivant
            </button>
          </div>
        </section>

        <aside v-if="!detail.is_creator" class="hive-members-aside stack" data-testid="hive-my-quota-aside">
          <section
            class="hive-block panel stack hive-my-quota-panel"
            aria-labelledby="hive-my-quota-title"
            data-testid="hive-my-quota-panel"
          >
            <h3 id="hive-my-quota-title" class="settings-subsection-title">Votre quota cette semaine</h3>
            <p v-if="myQuotaError" class="error hive-my-quota-error" role="alert" data-testid="hive-my-quota-error">
              {{ myQuotaError }}
            </p>
            <p v-if="myQuota?.week_start" class="hint hive-my-quota-intro" data-testid="hive-my-quota-intro">
              Volume reçu depuis chaque source qui vous applique un quota,
              {{ weekHeading(myQuota.week_start) }}. Lecture seule.
            </p>
            <p v-else-if="myQuotaLoading" class="hint">Chargement du quota…</p>
            <ul v-if="myQuota?.sources?.length" class="hive-my-quota-sources stack tight" data-testid="hive-my-quota-sources">
              <li
                v-for="src in myQuota.sources"
                :key="src.source_instance_id"
                class="hive-my-quota-card stack tight"
                data-testid="hive-my-quota-card"
              >
                <h4 class="hive-my-quota-source-name">Chez {{ src.source_display_name }}</h4>
                <template v-if="src.status === 'unavailable'">
                  <p class="hint hive-my-quota-unavailable" data-testid="hive-my-quota-unavailable">
                    Source indisponible pour le moment.
                  </p>
                </template>
                <template v-else-if="src.status === 'no_quota'">
                  <p class="hint">Partage dans la ruche · pas de quota applicable</p>
                </template>
                <template v-else>
                  <div class="hive-quota-line row sp">
                    <span>
                      Reçu cette semaine
                      {{ formatGoFr(src.bytes_used ?? 0) }} /
                      {{ formatGoFr(src.quota_bytes ?? 0) }}
                    </span>
                    <span
                      class="hive-quota-status-pill"
                      :class="`hive-quota-status-pill--${src.receive_quota_status || 'normal'}`"
                      data-testid="hive-my-quota-status"
                    >
                      {{ quotaStatusLabel(src.receive_quota_status) }}
                    </span>
                  </div>
                  <div
                    class="hive-quota-bar"
                    role="progressbar"
                    :aria-valuenow="myQuotaUsagePct(src)"
                    aria-valuemin="0"
                    aria-valuemax="100"
                    data-testid="hive-my-quota-bar"
                  >
                    <i
                      class="hive-quota-bar-fill"
                      :class="`hive-quota-bar-fill--${src.receive_quota_status || 'normal'}`"
                      :style="{ width: `${myQuotaUsagePct(src)}%` }"
                    />
                  </div>
                  <p v-if="src.reset_at" class="hint hive-member-reset">
                    {{
                      quotaResetLabel(
                        src.reset_at,
                        src.receive_quota_status === "exceeded",
                      )
                    }}
                  </p>
                </template>
              </li>
            </ul>
            <p v-else-if="myQuota && !myQuotaLoading" class="hint">
              Aucune source joignable pour afficher votre quota.
            </p>
            <p class="hint hive-my-quota-owner-note">
              Le propriétaire peut remettre à zéro le compteur de sa propre instance. Cela n'efface pas le compteur des
              autres sources.
            </p>
          </section>
        </aside>

        <aside v-if="detail.is_creator" class="hive-members-aside stack">
          <section
            class="hive-block panel stack hive-quota-ruche"
            aria-labelledby="hive-quota-ruche-title"
            data-testid="hive-quota-ruche-panel"
          >
            <h3 id="hive-quota-ruche-title" class="settings-subsection-title">
              Quota hebdomadaire de la ruche
            </h3>
            <p class="hint">
              Plafond pour les membres qui ne partagent aucun fichier dans la ruche. Compteur local
              sur cette instance (octets envoyés, transferts interrompus compris).
            </p>
            <label class="hive-field">
              <span class="label">Quota (Go)</span>
              <input
                v-model.number="quotaGb"
                type="number"
                min="1"
                step="1"
                class="input"
                data-testid="hive-quota-ruche-gb"
              />
            </label>
            <button type="button" class="btn btn-sm" data-testid="hive-save-quota" @click="saveQuota">
              Enregistrer
            </button>
            <p v-if="detail.quota_reset_at" class="hint hive-quota-ruche-reset">
              {{ quotaResetLabel(detail.quota_reset_at, false) }}
              <span class="hive-quota-utc">(lundi 00:00 UTC)</span>
            </p>
            <p class="hint hive-quota-ruche-owner-note">
              La remise à zéro d'un membre ne change pas ce plafond et ne concerne que le compteur de cette
              instance.
            </p>
          </section>
        </aside>
      </div>

      <HiveCatalogResetButton
        v-if="detail.is_creator"
        :ruche-id-hex="rucheIdHex"
        layout="zone"
        @success="onCatalogResetSuccess"
        @error="onCatalogResetError"
      />
    </template>

    <Teleport to="body">
      <div
        v-if="quotaResetMember"
        class="hive-quota-reset-backdrop"
        role="presentation"
        data-testid="hive-member-quota-reset-dialog"
        @click.self="closeQuotaResetDialog"
      >
        <div
          class="hive-quota-reset-dialog panel stack"
          role="dialog"
          aria-modal="true"
          aria-labelledby="hive-member-quota-reset-title"
        >
          <h3 id="hive-member-quota-reset-title">Remettre le quota à zéro ?</h3>
          <p class="hive-quota-reset-body">{{ quotaResetBody }}</p>
          <div class="hive-quota-reset-actions row sp">
            <button
              type="button"
              class="btn"
              :disabled="quotaResetBusy"
              data-testid="hive-member-quota-reset-cancel"
              @click="closeQuotaResetDialog"
            >
              Annuler
            </button>
            <button
              type="button"
              class="btn primary"
              :disabled="quotaResetBusy"
              data-testid="hive-member-quota-reset-submit"
              @click="submitQuotaReset"
            >
              Remettre à zéro
            </button>
          </div>
        </div>
      </div>
    </Teleport>
  </div>
</template>

<style scoped>
.hive-ruche-members {
  max-width: 72rem;
  gap: 1rem;
  padding-bottom: 2rem;
}

.hive-members-layout {
  display: grid;
  gap: 1rem;
}

.hive-members-aside {
  gap: 0.75rem;
}

.hive-members-week,
.hive-members-non-creator {
  margin: 0;
  font-size: 0.85rem;
}

.hive-quota-reset-backdrop {
  position: fixed;
  inset: 0;
  z-index: 120;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 1rem;
  padding-bottom: max(1rem, env(safe-area-inset-bottom, 0px) + 4.5rem);
  background: color-mix(in srgb, var(--bg) 35%, transparent);
}

.hive-quota-reset-dialog {
  width: min(100%, 28rem);
  max-height: min(90vh, 100%);
  overflow: auto;
  padding: 1.1rem 1.2rem;
  gap: 0.85rem;
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  background: var(--card);
  box-shadow: 0 12px 40px color-mix(in srgb, var(--bg) 50%, transparent);
}

.hive-quota-reset-body {
  margin: 0;
  font-size: 0.92rem;
  white-space: pre-line;
}

.hive-quota-reset-actions {
  gap: 0.5rem;
  flex-wrap: wrap;
}

.hive-quota-ruche-owner-note {
  margin: 0;
  font-size: 0.82rem;
}

.hive-member-share,
.hive-member-quota-intro {
  margin: 0;
  font-size: 0.88rem;
}

.hive-quota-line {
  align-items: center;
  flex-wrap: wrap;
  gap: 0.5rem;
  font-size: 0.92rem;
}

.hive-quota-status-pill {
  font-size: 0.78rem;
  padding: 0.1rem 0.45rem;
  border-radius: 999px;
  border: 1px solid var(--hair);
}

.hive-quota-status-pill--normal {
  background: color-mix(in srgb, var(--ok) 12%, var(--card));
  border-color: color-mix(in srgb, var(--ok) 35%, var(--hair));
}

.hive-quota-status-pill--near {
  background: color-mix(in srgb, var(--warn) 14%, var(--card));
  border-color: color-mix(in srgb, var(--warn) 40%, var(--hair));
}

.hive-quota-status-pill--exceeded {
  background: color-mix(in srgb, var(--danger) 14%, var(--card));
  border-color: color-mix(in srgb, var(--danger) 40%, var(--hair));
  color: color-mix(in srgb, var(--danger) 85%, var(--text));
}

.hive-quota-bar {
  height: 0.35rem;
  border-radius: 999px;
  background: color-mix(in srgb, var(--muted) 25%, var(--bg));
  overflow: hidden;
}

.hive-quota-bar-fill {
  display: block;
  height: 100%;
  border-radius: inherit;
  transition: width 0.2s ease;
}

.hive-quota-bar-fill--normal {
  background: var(--ok);
}

.hive-quota-bar-fill--near {
  background: var(--warn);
}

.hive-quota-bar-fill--exceeded {
  background: var(--danger);
}

.hive-member-reset,
.hive-quota-ruche-reset {
  margin: 0;
  font-size: 0.82rem;
}

.hive-quota-utc {
  display: block;
  margin-top: 0.15rem;
}

.hive-quota-ruche {
  gap: 0.65rem;
}

.hive-my-quota-panel {
  gap: 0.65rem;
}

.hive-my-quota-sources {
  list-style: none;
  margin: 0;
  padding: 0;
  gap: 0.85rem;
}

.hive-my-quota-card {
  padding: 0.75rem 0.85rem;
  border: 1px solid var(--hair);
  border-radius: var(--radius-sm);
  background: color-mix(in srgb, var(--bg) 40%, var(--card));
}

.hive-my-quota-source-name {
  margin: 0;
  font-size: 0.95rem;
  font-weight: 600;
}

.hive-my-quota-intro,
.hive-my-quota-owner-note {
  margin: 0;
  font-size: 0.85rem;
}

@media (min-width: 900px) {
  .hive-members-layout {
    grid-template-columns: minmax(0, 1fr) minmax(14rem, 18rem);
    align-items: start;
  }
}

.hive-block.panel {
  gap: 0.75rem;
  padding: 1rem 1.15rem;
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  background: var(--card);
}

.hive-members-toolbar {
  align-items: flex-end;
  gap: 0.75rem;
  flex-wrap: wrap;
}

.hive-members-search {
  min-width: min(100%, 14rem);
}

.hive-field {
  display: grid;
  gap: 0.35rem;
  margin: 0;
}

.hive-field .label {
  font-size: 0.82rem;
  color: var(--muted);
}

.hive-member-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: grid;
  gap: 0.55rem;
}

.hive-member-row {
  padding: 0.65rem 0.75rem;
  border: 1px solid var(--hair);
  border-radius: var(--radius-sm);
  background: color-mix(in srgb, var(--bg) 90%, var(--card));
  gap: 0.5rem;
}

.hive-member-main {
  align-items: flex-start;
  flex-wrap: wrap;
  gap: 0.5rem;
}

.hive-member-identity {
  min-width: 0;
  flex: 1 1 12rem;
}

.hive-member-name {
  word-break: break-word;
}

.hive-version-pill {
  display: inline-block;
  width: fit-content;
  max-width: 100%;
  font-size: 0.78rem;
  line-height: 1.35;
  padding: 0.12rem 0.45rem;
  border-radius: 999px;
  border: 1px solid var(--hair);
  white-space: normal;
}

.hive-version-pill--muted {
  color: var(--muted);
  background: color-mix(in srgb, var(--bg) 92%, var(--card));
}

.hive-version-pill--ok {
  color: var(--muted);
  background: color-mix(in srgb, var(--ok) 12%, var(--card));
  border-color: color-mix(in srgb, var(--ok) 35%, var(--hair));
}

.hive-version-pill--warn {
  color: color-mix(in srgb, var(--warn) 85%, var(--text));
  background: color-mix(in srgb, var(--warn) 14%, var(--card));
  border-color: color-mix(in srgb, var(--warn) 40%, var(--hair));
}

.hive-self-version {
  margin: 0;
  font-size: 0.85rem;
}

.hive-members-pagination {
  margin-top: 0.35rem;
}

.stack.tight {
  gap: 0.2rem;
}

.hive-catalog-reset-notice {
  margin: 0;
  font-size: 0.88rem;
}

@media (max-width: 720px) {
  .hive-members-toolbar {
    flex-direction: column;
    align-items: stretch;
  }
}
</style>
