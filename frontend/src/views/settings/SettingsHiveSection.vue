<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { RouterLink, useRoute, useRouter } from "vue-router";

import { api, userFacingApiError } from "../../api";
import { useHiveEnabled } from "../../composables/useHive";
import {
  hiveTransferMinutesToTime,
  hiveTransferTimeToMinutes,
  HIVE_TRANSFER_ALL_DAY_END,
  HIVE_TRANSFER_ALL_DAY_START,
  isHiveTransferAllDay,
} from "../../hive/hiveTransferWindow";
import { fetchSettings, patchSettings } from "../../settings";

const router = useRouter();
const route = useRoute();
const { hiveEnabled, refreshHive } = useHiveEnabled();

interface Identity {
  enabled: boolean;
  identity_lost: boolean;
  identity_lost_message: string | null;
  instance_id: string | null;
  name: string | null;
  public_address: string | null;
  pull_only: boolean;
}

interface RucheRow {
  ruche_id_hex: string;
  member_count: number;
  is_creator: boolean;
  pending_join_count?: number;
}

const hiveOn = ref(false);
const publicAddress = ref("");
const identity = ref<Identity | null>(null);
const ruches = ref<RucheRow[]>([]);
const error = ref("");
const note = ref("");
const activationOpen = ref(false);
const activationName = ref("");
const resetOpen = ref(false);
const resetName = ref("");
const inviteLink = ref("");
const transferParallel = ref(2);
const transferSend = ref(0);
const transferRecv = ref(0);
const transferWinStart = ref(0);
const transferWinEnd = ref(24 * 60);
const transferWinStartTime = ref("00:00");
const transferWinEndTime = ref("00:00");
const catalogAutoShare = ref(false);

const transferWinAllDay = computed({
  get: () => isHiveTransferAllDay(transferWinStart.value, transferWinEnd.value),
  set: (on: boolean) => {
    if (on) {
      transferWinStart.value = HIVE_TRANSFER_ALL_DAY_START;
      transferWinEnd.value = HIVE_TRANSFER_ALL_DAY_END;
      transferWinStartTime.value = hiveTransferMinutesToTime(HIVE_TRANSFER_ALL_DAY_START);
      transferWinEndTime.value = hiveTransferMinutesToTime(HIVE_TRANSFER_ALL_DAY_END);
      return;
    }
    if (isHiveTransferAllDay(transferWinStart.value, transferWinEnd.value)) {
      transferWinStart.value = HIVE_TRANSFER_ALL_DAY_START;
      transferWinEnd.value = 23 * 60;
      transferWinStartTime.value = hiveTransferMinutesToTime(transferWinStart.value);
      transferWinEndTime.value = hiveTransferMinutesToTime(transferWinEnd.value);
    }
  },
});

function syncTransferWindowFieldsFromMinutes() {
  transferWinStartTime.value = hiveTransferMinutesToTime(transferWinStart.value);
  transferWinEndTime.value = hiveTransferMinutesToTime(transferWinEnd.value);
}

function applyTransferWindowTimesToMinutes() {
  if (transferWinAllDay.value) {
    transferWinStart.value = HIVE_TRANSFER_ALL_DAY_START;
    transferWinEnd.value = HIVE_TRANSFER_ALL_DAY_END;
    return;
  }
  transferWinStart.value = hiveTransferTimeToMinutes(transferWinStartTime.value);
  transferWinEnd.value = Math.max(1, hiveTransferTimeToMinutes(transferWinEndTime.value));
}

function onTransferStartTimeInput(event: Event) {
  const input = event.target;
  if (!(input instanceof HTMLInputElement)) {
    return;
  }
  transferWinStartTime.value = input.value;
  if (transferWinAllDay.value) {
    transferWinAllDay.value = false;
  }
  applyTransferWindowTimesToMinutes();
}

function onTransferEndTimeInput(event: Event) {
  const input = event.target;
  if (!(input instanceof HTMLInputElement)) {
    return;
  }
  transferWinEndTime.value = input.value;
  if (transferWinAllDay.value) {
    transferWinAllDay.value = false;
  }
  applyTransferWindowTimesToMinutes();
}

const pullOnlyHint = computed(
  () => identity.value?.pull_only && "Mode tirage : vous recevez, sans envoyer.",
);

async function load() {
  error.value = "";
  try {
    const page = await fetchSettings();
    const map = new Map(page.settings?.map((row) => [row.key, row.value]));
    hiveOn.value = map.get("hive_enabled") === true || map.get("hive_enabled") === "true";
    publicAddress.value = String(map.get("hive_public_address") || "");
    transferParallel.value = Number(map.get("hive_transfer_parallel") ?? 2);
    transferSend.value = Number(map.get("hive_transfer_send_bps") ?? 0);
    transferRecv.value = Number(map.get("hive_transfer_recv_bps") ?? 0);
    transferWinStart.value = Number(map.get("hive_transfer_window_start") ?? 0);
    transferWinEnd.value = Number(map.get("hive_transfer_window_end") ?? 24 * 60);
    syncTransferWindowFieldsFromMinutes();
    catalogAutoShare.value =
      map.get("hive_catalog_auto_share") === true || map.get("hive_catalog_auto_share") === "true";
    if (hiveOn.value) {
      identity.value = await api<Identity>("/api/v1/hive/identity");
      ruches.value = await api<RucheRow[]>("/api/v1/hive/ruches");
    } else {
      ruches.value = [];
    }
  } catch (err) {
    error.value = userFacingApiError(err, "Impossible de charger Hive.");
  }
}

onMounted(load);

async function syncNavAfterHiveChange() {
  await refreshHive();
  if (!hiveEnabled.value && route.path.startsWith("/hive")) {
    await router.replace("/reglages/hive");
  }
}

async function saveCore() {
  note.value = "";
  error.value = "";
  try {
    applyTransferWindowTimesToMinutes();
    await patchSettings({
      values: {
        hive_enabled: hiveOn.value,
        hive_public_address: publicAddress.value.trim(),
        hive_transfer_parallel: transferParallel.value,
        hive_transfer_send_bps: transferSend.value,
        hive_transfer_recv_bps: transferRecv.value,
        hive_transfer_window_start: transferWinStart.value,
        hive_transfer_window_end: transferWinEnd.value,
        hive_catalog_auto_share: catalogAutoShare.value,
      },
    });
    await load();
    await syncNavAfterHiveChange();
    note.value = "Réglages enregistrés.";
  } catch (err) {
    const message = userFacingApiError(err, "Échec de l’enregistrement.");
    await load();
    await refreshHive();
    error.value = message;
  }
}

async function confirmActivation() {
  if (!activationName.value.trim()) {
    error.value = "Le nom d’instance est obligatoire.";
    return;
  }
  note.value = "";
  error.value = "";
  try {
    await patchSettings({
      values: {
        hive_enabled: true,
        hive_activation_name: activationName.value.trim(),
        hive_public_address: publicAddress.value.trim(),
      },
    });
    activationOpen.value = false;
    activationName.value = "";
    await load();
    await syncNavAfterHiveChange();
    note.value = "Hive activé.";
  } catch (err) {
    const message = userFacingApiError(err, "Activation impossible.");
    await load();
    await refreshHive();
    error.value = message;
  }
}

async function persistHiveToggle(enabled: boolean) {
  note.value = "";
  error.value = "";
  try {
    await patchSettings({ values: { hive_enabled: enabled } });
    await load();
    await syncNavAfterHiveChange();
    note.value = enabled ? "Hive activé." : "Hive désactivé.";
  } catch (err) {
    const message = userFacingApiError(err, enabled ? "Activation impossible." : "Désactivation impossible.");
    await load();
    await refreshHive();
    error.value = message;
  }
}

async function onToggleHive() {
  if (hiveOn.value) {
    if (!identity.value?.name) {
      const page = await fetchSettings();
      const map = new Map(page.settings?.map((row) => [row.key, row.value]));
      const priorName = String(map.get("hive_instance_name") || "").trim();
      if (priorName) {
        await persistHiveToggle(true);
        return;
      }
      activationOpen.value = true;
      hiveOn.value = false;
      return;
    }
    void persistHiveToggle(true);
    return;
  }
  void persistHiveToggle(false);
}

async function copyId() {
  if (!identity.value?.instance_id) {
    return;
  }
  try {
    await navigator.clipboard.writeText(identity.value.instance_id);
    note.value = "Identifiant copié.";
  } catch {
    note.value = identity.value.instance_id;
  }
}

async function createRuche() {
  try {
    await api("/api/v1/hive/ruches", { method: "POST" });
    await load();
  } catch (err) {
    error.value = userFacingApiError(err, "Création de ruche impossible.");
  }
}

function membresPath(rucheId: string) {
  return `/hive/ruches/${rucheId}/membres`;
}

async function leaveRuche(rucheId: string) {
  try {
    await api(`/api/v1/hive/ruches/${rucheId}/leave`, { method: "POST" });
    await load();
  } catch (err) {
    error.value = userFacingApiError(err, "Départ impossible.");
  }
}

async function invite(rucheId: string) {
  try {
    const row = await api<{ link: string }>("/api/v1/hive/ruches/invitations", {
      method: "POST",
      body: JSON.stringify({ ruche_id_hex: rucheId }),
    });
    inviteLink.value = row.link;
  } catch (err) {
    error.value = userFacingApiError(err, "Invitation impossible.");
  }
}

async function confirmReset() {
  if (!resetName.value.trim()) {
    return;
  }
  try {
    await api("/api/v1/hive/identity/reset", {
      method: "POST",
      body: JSON.stringify({ name: resetName.value.trim() }),
    });
    resetOpen.value = false;
    resetName.value = "";
    await load();
  } catch (err) {
    error.value = userFacingApiError(err, "Réinitialisation impossible.");
  }
}
</script>

<template>
  <div class="settings-hive zone stack">
    <p v-if="error" class="error" role="alert">{{ error }}</p>
    <p v-if="note" class="ok">{{ note }}</p>

    <section class="hive-block panel stack" aria-labelledby="hive-act-title">
      <h2 id="hive-act-title" class="settings-section-title">Activation</h2>
      <p class="hive-block-lead hint">Interrupteur maître : coupe tout partage et échange. L’entrée Hive n’apparaît que lorsqu’il est activé.</p>
      <label class="hive-toggle row sp">
        <span>Hive</span>
        <span class="row">
          <input v-model="hiveOn" type="checkbox" data-testid="hive-master-toggle" @change="onToggleHive" />
          <span>{{ hiveOn ? "Activé" : "Désactivé" }}</span>
        </span>
      </label>
    </section>

    <section v-if="activationOpen" class="hive-block panel stack hive-modal" aria-labelledby="hive-first-act">
      <h2 id="hive-first-act" class="settings-section-title">Première activation</h2>
      <p class="warn">
        Ce nom sera définitif ; pour le changer, il faudra réinitialiser l’identité et être réinvité dans chaque ruche.
      </p>
      <label class="label">Nom d’instance</label>
      <input v-model="activationName" class="input block" maxlength="32" />
      <div class="row">
        <button type="button" class="btn primary" @click="confirmActivation">Activer Hive</button>
        <button type="button" class="btn" @click="activationOpen = false">Annuler</button>
      </div>
    </section>

    <template v-if="hiveOn && identity">
      <section class="hive-block panel stack" aria-labelledby="hive-id-title">
        <h2 id="hive-id-title" class="settings-section-title">Identité</h2>
        <div class="hive-field-grid">
          <p class="hive-field">
            <span class="label">Nom</span>
            <strong>{{ identity.name }} <span aria-hidden="true">🔒</span></strong>
          </p>
          <p class="hive-field">
            <span class="label">Identifiant</span>
            <code class="token">{{ identity.instance_id }}</code>
          </p>
          <button type="button" class="btn btn-sm" @click="copyId">Copier l’identifiant</button>
        </div>
        <label class="hive-field">
          <span class="label">Adresse publique</span>
          <input v-model="publicAddress" class="input block" placeholder="https://mtr.exemple.fr" />
        </label>
        <p v-if="pullOnlyHint" class="tag warn">{{ pullOnlyHint }}</p>
      </section>

      <section class="hive-block panel stack" aria-labelledby="hive-ruches-title">
        <div class="row sp hive-block-head">
          <div>
            <h2 id="hive-ruches-title" class="settings-section-title">Mes ruches</h2>
            <p class="hive-block-lead hint">Création, invitations, membres, quota créateur et départ.</p>
          </div>
          <div class="row hive-ruche-actions">
            <button type="button" class="btn btn-sm" @click="createRuche">Créer une ruche</button>
            <RouterLink class="btn btn-sm" to="/hive/rejoindre">Rejoindre avec un lien</RouterLink>
          </div>
        </div>
        <ul class="hive-ruche-list">
          <li v-for="r in ruches" :key="r.ruche_id_hex" class="hive-ruche-item">
            <div class="row sp">
              <div class="row hive-ruche-summary">
                <span class="hive-ruche-label">
                  Ruche {{ r.ruche_id_hex.slice(0, 8) }}… · {{ r.member_count }} membre(s)
                </span>
                <span
                  v-if="r.is_creator && (r.pending_join_count ?? 0) > 0"
                  class="tag warn hive-pending-badge"
                  data-testid="hive-pending-badge"
                >
                  {{ r.pending_join_count }} demande(s)
                </span>
              </div>
              <div class="row hive-ruche-item-actions">
                <RouterLink class="btn btn-sm" :to="membresPath(r.ruche_id_hex)" data-testid="hive-membres-link">
                  Membres
                </RouterLink>
                <button v-if="r.is_creator" type="button" class="btn btn-sm" @click="invite(r.ruche_id_hex)">Inviter</button>
                <button v-if="!r.is_creator" type="button" class="btn btn-sm danger" @click="leaveRuche(r.ruche_id_hex)">Quitter</button>
              </div>
            </div>
          </li>
        </ul>
        <p v-if="inviteLink" class="token hive-invite">Lien d’invitation : {{ inviteLink }}</p>
      </section>

      <section class="hive-block panel stack" aria-labelledby="hive-share-title">
        <h2 id="hive-share-title" class="settings-section-title">Partage reçu</h2>
        <label class="hive-field row sp">
          <span>
            <span class="label">Repartager automatiquement les fichiers reçus via Hive</span>
            <span class="hint block">
              Désactivé par défaut : un fichier reçu via Hive n’enregistre pas d’intention de partage tant que ce réglage est off.
            </span>
          </span>
          <span class="row">
            <input
              v-model="catalogAutoShare"
              type="checkbox"
              data-testid="hive-catalog-auto-share-toggle"
            />
            <span>{{ catalogAutoShare ? "Activé" : "Désactivé" }}</span>
          </span>
        </label>
      </section>

      <section class="hive-block panel stack" aria-labelledby="hive-limits-title">
        <h2 id="hive-limits-title" class="settings-section-title">Limites de transfert</h2>
        <div class="hive-limits-grid">
          <label class="hive-field">
            <span class="label">Transferts simultanés</span>
            <input v-model.number="transferParallel" type="number" min="1" max="16" class="input" />
          </label>
          <label class="hive-field">
            <span class="label">Débit envoi (o/s, 0 = illimité)</span>
            <input v-model.number="transferSend" type="number" min="0" class="input" />
          </label>
          <label class="hive-field">
            <span class="label">Débit réception (o/s)</span>
            <input v-model.number="transferRecv" type="number" min="0" class="input" />
          </label>
          <fieldset class="hive-field hive-transfer-window" data-testid="hive-transfer-window">
            <legend class="label">Fenêtre horaire de transfert</legend>
            <label class="row sp">
              <span>Toute la journée</span>
              <input v-model="transferWinAllDay" type="checkbox" data-testid="hive-transfer-all-day" />
            </label>
            <div class="hive-transfer-times">
              <label>
                <span class="label">De</span>
                <input
                  :value="transferWinStartTime"
                  type="time"
                  class="input"
                  step="60"
                  :disabled="transferWinAllDay"
                  data-testid="hive-transfer-start"
                  @input="onTransferStartTimeInput"
                />
              </label>
              <label>
                <span class="label">À</span>
                <input
                  :value="transferWinEndTime"
                  type="time"
                  class="input"
                  step="60"
                  :disabled="transferWinAllDay"
                  data-testid="hive-transfer-end"
                  @input="onTransferEndTimeInput"
                />
              </label>
            </div>
          </fieldset>
          <p class="hint">
            Fuseau local de l’instance (<code>TZ</code> du conteneur, par défaut Europe/Paris). Une plage peut
            passer minuit (ex. 22:00 → 06:00).
          </p>
        </div>
        <button type="button" class="btn primary" @click="saveCore">Enregistrer les réglages Hive</button>
      </section>

      <section class="hive-block panel stack hive-danger-block" aria-labelledby="hive-reset-title">
        <h2 id="hive-reset-title" class="settings-section-title bad">Réinitialisation</h2>
        <p class="hive-block-lead hint">
          Nouvelle identité : vous quittez toutes les ruches et devez être réinvité. Action irréversible sans confirmation du nom.
        </p>
        <button type="button" class="btn danger" @click="resetOpen = true">Réinitialiser l’identité…</button>
      </section>
    </template>

    <section v-else-if="!hiveOn" class="hive-block panel stack">
      <h2 class="settings-section-title">Hive désactivé</h2>
      <p class="hive-block-lead hint">Aucun échange ni entrée de navigation Hive.</p>
      <button type="button" class="btn primary" @click="activationOpen = true">Activer Hive…</button>
    </section>

    <section v-if="resetOpen" class="hive-block panel stack hive-modal" aria-labelledby="hive-reset-modal">
      <h2 id="hive-reset-modal" class="settings-section-title bad">Confirmer la réinitialisation</h2>
      <p>Vous quitterez toutes les ruches : {{ ruches.map((r) => r.ruche_id_hex.slice(0, 8)).join(", ") || "—" }}</p>
      <label class="label">Nouveau nom</label>
      <input v-model="resetName" class="input block" maxlength="32" />
      <div class="row">
        <button type="button" class="btn danger" @click="confirmReset">Confirmer</button>
        <button type="button" class="btn" @click="resetOpen = false">Annuler</button>
      </div>
    </section>
  </div>
</template>

<style scoped>
.settings-hive {
  max-width: 52rem;
  gap: 1.15rem;
  padding-bottom: 1.5rem;
}

@media (max-width: 720px) {
  .settings-hive {
    padding-bottom: 5.75rem;
  }
}

.hive-block.panel {
  gap: 0.75rem;
  padding: 1rem 1.15rem;
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  background: var(--card);
}

.hive-block-lead {
  margin: -0.35rem 0 0.25rem;
}

.hive-block-head {
  align-items: flex-start;
  gap: 0.75rem;
}

.hive-field-grid {
  display: grid;
  gap: 0.65rem;
}

.hive-field {
  display: grid;
  gap: 0.35rem;
  margin: 0;
}

.hive-transfer-times {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 0.65rem;
}

.hive-transfer-window {
  border: 0;
  padding: 0;
  margin: 0;
}

.hive-transfer-window legend {
  margin-bottom: 0.35rem;
}

.hive-field .label {
  font-size: 0.82rem;
  color: var(--muted);
}

.hive-toggle {
  padding-top: 0.25rem;
}

.hive-ruche-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: grid;
  gap: 0.65rem;
}

.hive-ruche-item {
  padding: 0.75rem 0.85rem;
  border: 1px solid var(--hair);
  border-radius: 0.45rem;
  background: color-mix(in srgb, var(--bg) 88%, var(--card));
}

.hive-ruche-summary {
  gap: 0.5rem;
  flex-wrap: wrap;
  align-items: center;
}

.hive-ruche-item-actions {
  flex-wrap: wrap;
  justify-content: flex-end;
  gap: 0.35rem;
}

.hive-pending-badge {
  font-size: 0.78rem;
}

.hive-limits-grid {
  display: grid;
  gap: 0.75rem;
}

.hive-ruche-actions {
  flex-wrap: wrap;
  justify-content: flex-end;
}

.hive-danger-block {
  border-color: color-mix(in srgb, var(--danger) 35%, var(--border));
}

.hive-invite {
  margin-top: 0.5rem;
}

.token {
  font-family: monospace;
  word-break: break-all;
  font-size: 0.85rem;
}

.bad {
  color: var(--danger);
}

@media (max-width: 720px) {
  .hive-block-head {
    flex-direction: column;
  }

  .hive-ruche-actions {
    width: 100%;
    justify-content: flex-start;
  }
}
</style>
