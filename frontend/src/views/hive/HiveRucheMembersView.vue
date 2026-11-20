<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue";
import { RouterLink, useRoute } from "vue-router";

import { api, userFacingApiError } from "../../api";
import HiveCatalogResetButton from "../../components/hive/HiveCatalogResetButton.vue";
import HivePendingJoinList, { type PendingJoinRow } from "../../components/hive/HivePendingJoinList.vue";

const MEMBERS_PAGE_SIZE = 20;

interface MemberRow {
  instance_id: string;
  display_name: string;
  pull_only: boolean;
  is_self: boolean;
  app_version: string | null;
  hive_protocol: number;
  capabilities: string[];
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
  catalog_epoch: number;
  catalog_reset_at: string | null;
  members: MemberRow[];
}

const HIVE_PROTOCOL_FALLBACK = 1;

const route = useRoute();
const rucheIdHex = computed(() => String(route.params.rucheIdHex || ""));
const detail = ref<RucheDetail | null>(null);

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

async function loadDetail() {
  loading.value = true;
  error.value = "";
  try {
    detail.value = await api<RucheDetail>(`/api/v1/hive/ruches/${rucheIdHex.value}/detail`);
    quotaGb.value = Math.round((detail.value.weekly_quota_bytes / 1024 ** 3) * 10) / 10;
    await loadPending();
  } catch (err) {
    detail.value = null;
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
                  <span v-if="m.pull_only" class="hint">(tirage)</span>
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
            <div
              v-if="detail.is_creator && !m.is_self"
              class="hive-member-options row sp"
              data-testid="hive-member-options"
            >
              <label class="hive-field">
                <span class="label">Quota hebdo (Go)</span>
                <input v-model.number="quotaGb" type="number" min="1" step="1" class="input" />
              </label>
              <button type="button" class="btn btn-sm" data-testid="hive-save-quota" @click="saveQuota">
                Enregistrer quota
              </button>
            </div>
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

      <HiveCatalogResetButton
        v-if="detail.is_creator"
        :ruche-id-hex="rucheIdHex"
        layout="zone"
        @success="onCatalogResetSuccess"
        @error="onCatalogResetError"
      />
    </template>
  </div>
</template>

<style scoped>
.hive-ruche-members {
  max-width: 52rem;
  gap: 1rem;
  padding-bottom: 2rem;
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

.hive-member-options {
  align-items: flex-end;
  flex-wrap: wrap;
  gap: 0.5rem;
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
