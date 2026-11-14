<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from "vue";

import { api, userFacingApiError } from "../api";

interface PinView {
  pin: string | null;
  user_url: string | null;
  expires_in: number | null;
}

interface AccountState {
  connected: boolean;
  source: "env" | "db" | "none";
  env_locked: boolean;
  pin_phase: "idle" | "pending" | "expired";
  pin: PinView | null;
  pin_message: string | null;
  pin_error: string | null;
  env_notice: string | null;
  account_notice: string | null;
}

const loading = ref(true);
const actionError = ref("");
const account = ref<AccountState | null>(null);
const manualOpen = ref(false);
const manualKey = ref("");
const manualSaving = ref(false);
const expiresLeft = ref<number | null>(null);
const POLL_MS = 5000;

let pollTimer: ReturnType<typeof setTimeout> | null = null;
let tickTimer: ReturnType<typeof setInterval> | null = null;
let pollInFlight = false;
let pausedForHidden = false;
let pollGeneration = 0;

const pinLetters = computed(() => {
  const raw = account.value?.pin?.pin ?? "";
  return raw.split("").filter(Boolean);
});

const showPending = computed(
  () => !account.value?.env_locked && account.value?.pin_phase === "pending" && account.value?.pin?.pin,
);

const showExpired = computed(() => !account.value?.env_locked && account.value?.pin_phase === "expired");

const showDisconnected = computed(
  () =>
    !account.value?.env_locked &&
    !account.value?.connected &&
    account.value?.pin_phase === "idle" &&
    !showPending.value,
);

const showConnected = computed(() => Boolean(account.value?.connected));

const countdownLabel = computed(() => {
  const left = expiresLeft.value;
  if (left == null || left < 0) return "—";
  const m = Math.floor(left / 60);
  const s = left % 60;
  return `${m}:${String(s).padStart(2, "0")}`;
});

const alldebridPinUrl = computed(() => {
  const url = account.value?.pin?.user_url;
  return url && url.startsWith("https://alldebrid.com/") ? url : "https://alldebrid.com/pin/";
});

function clearPollTimer() {
  if (pollTimer !== null) {
    clearTimeout(pollTimer);
    pollTimer = null;
  }
}

function stopTick() {
  if (tickTimer) {
    clearInterval(tickTimer);
    tickTimer = null;
  }
}

function stopPoll() {
  pollGeneration += 1;
  clearPollTimer();
  pollInFlight = false;
}

function syncExpiresFromAccount() {
  const raw = account.value?.pin?.expires_in;
  expiresLeft.value = typeof raw === "number" ? raw : null;
}

function startTick() {
  stopTick();
  if (expiresLeft.value == null) return;
  tickTimer = setInterval(() => {
    if (expiresLeft.value == null) return;
    if (expiresLeft.value > 0) {
      expiresLeft.value -= 1;
      return;
    }
    stopTick();
    stopPoll();
    void loadAccount();
  }, 1000);
}

async function loadAccount() {
  actionError.value = "";
  try {
    account.value = await api<AccountState>("/api/v1/integrations/alldebrid/account");
    if (showPending.value) {
      syncExpiresFromAccount();
      startTick();
    } else {
      stopTick();
      expiresLeft.value = null;
    }
  } catch (err) {
    actionError.value = userFacingApiError(err, "Lecture du compte impossible.");
  } finally {
    loading.value = false;
  }
}

async function pollOnce() {
  if (!showPending.value || pausedForHidden) return;
  if (expiresLeft.value === 0) {
    stopPoll();
    return;
  }
  const gen = pollGeneration;
  try {
    const next = await api<AccountState>("/api/v1/integrations/alldebrid/account/pin/poll", {
      method: "POST",
    });
    if (gen !== pollGeneration || !next) return;
    account.value = next;
    if (account.value.pin_phase === "pending" && account.value.pin?.expires_in != null) {
      expiresLeft.value = account.value.pin.expires_in;
    }
    if (account.value.connected || account.value.pin_phase !== "pending") {
      stopPoll();
      stopTick();
    }
  } catch (err) {
    if (gen === pollGeneration) {
      actionError.value = userFacingApiError(err, "Contrôle du code impossible.");
    }
  }
}

async function runPollCycle() {
  if (pollInFlight || pausedForHidden || !showPending.value) {
    schedulePoll();
    return;
  }
  pollInFlight = true;
  try {
    await pollOnce();
  } finally {
    pollInFlight = false;
    if (showPending.value && !pausedForHidden && expiresLeft.value !== 0) {
      schedulePoll(POLL_MS);
    }
  }
}

function schedulePoll(delay = 0) {
  clearPollTimer();
  if (!showPending.value || pausedForHidden) return;
  const gen = pollGeneration;
  pollTimer = setTimeout(() => {
    if (gen !== pollGeneration) return;
    void runPollCycle();
  }, delay);
}

function ensurePoll(immediate = false) {
  stopPoll();
  if (!showPending.value || pausedForHidden) return;
  schedulePoll(immediate ? 0 : POLL_MS);
}

function onVisibilityChange() {
  if (document.hidden) {
    pausedForHidden = true;
    clearPollTimer();
    return;
  }
  pausedForHidden = false;
  if (showPending.value) {
    void runPollCycle();
  }
}

async function startPin() {
  actionError.value = "";
  manualOpen.value = false;
  try {
    account.value = await api<AccountState>("/api/v1/integrations/alldebrid/account/pin/start", {
      method: "POST",
    });
    syncExpiresFromAccount();
    startTick();
    ensurePoll(true);
  } catch (err) {
    actionError.value = userFacingApiError(err, "Génération du code impossible.");
  }
}

async function cancelPin() {
  actionError.value = "";
  try {
    account.value = await api<AccountState>("/api/v1/integrations/alldebrid/account/pin/cancel", {
      method: "POST",
    });
    stopPoll();
    stopTick();
  } catch (err) {
    actionError.value = userFacingApiError(err, "Annulation impossible.");
  }
}

async function disconnect() {
  actionError.value = "";
  if (!window.confirm("Retirer la clé AllDebrid enregistrée dans MediaTracker ?")) {
    return;
  }
  try {
    account.value = await api<AccountState>("/api/v1/integrations/alldebrid/account/disconnect", {
      method: "POST",
    });
  } catch (err) {
    actionError.value = userFacingApiError(err, "Déconnexion impossible.");
  }
}

async function saveManual() {
  actionError.value = "";
  manualSaving.value = true;
  try {
    account.value = await api<AccountState>("/api/v1/integrations/alldebrid/account/manual", {
      method: "POST",
      body: JSON.stringify({ api_key: manualKey.value }),
    });
    manualKey.value = "";
    manualOpen.value = false;
  } catch (err) {
    actionError.value = userFacingApiError(err, "Enregistrement impossible.");
  } finally {
    manualSaving.value = false;
  }
}

async function copyPin() {
  const pin = account.value?.pin?.pin;
  if (!pin) return;
  try {
    await navigator.clipboard.writeText(pin);
  } catch {
    actionError.value = "Copie impossible sur cet appareil.";
  }
}

function openAllDebrid() {
  window.open(alldebridPinUrl.value, "_blank", "noopener,noreferrer");
}

watch(showPending, (pending) => {
  if (pending) {
    ensurePoll(true);
  } else {
    stopPoll();
  }
});

onMounted(() => {
  document.addEventListener("visibilitychange", onVisibilityChange);
  void loadAccount().then(() => ensurePoll(true));
});

onUnmounted(() => {
  document.removeEventListener("visibilitychange", onVisibilityChange);
  stopPoll();
  stopTick();
});
</script>

<template>
  <div v-if="!loading" class="alldebrid-account stack" data-testid="alldebrid-account-block">
    <h3 class="settings-subsection-title">Compte</h3>

    <p v-if="account?.account_notice" class="hint">{{ account.account_notice }}</p>
    <p v-if="account?.env_locked" class="hint">{{ account.env_notice }}</p>

    <template v-if="account?.env_locked && showConnected">
      <p class="alldebrid-status row">
        <span class="status-pill on"><span class="dot" aria-hidden="true" />Compte AllDebrid connecté</span>
      </p>
      <p class="hint">La clé est gérée dans la configuration du serveur. Elle n'est pas modifiable ici.</p>
    </template>

    <template v-else-if="showConnected">
      <p class="alldebrid-status row">
        <span class="status-pill on"><span class="dot" aria-hidden="true" />Compte AllDebrid connecté</span>
      </p>
      <p class="hint">La clé est stockée côté serveur. Elle n'est jamais affichée ici.</p>
      <button type="button" class="danger-ghost" data-testid="alldebrid-disconnect" @click="disconnect">
        Déconnecter
      </button>
    </template>

    <template v-else-if="showExpired">
      <div class="err-box" role="alert">
        {{ account?.pin_message || "Le code a expiré. Aucune clé n'a été enregistrée." }}
      </div>
      <button type="button" class="primary block" data-testid="alldebrid-retry-pin" @click="startPin">
        Réessayer
      </button>
      <button type="button" class="link-quiet" @click="manualOpen = true">Saisir la clé manuellement</button>
    </template>

    <template v-else-if="showPending">
      <p class="hint">
        Ouvrez la page AllDebrid, saisissez ce code, puis revenez ici. La clé reste sur le serveur.
      </p>
      <div class="pin-code" data-testid="alldebrid-pin-display">
        <span class="code" aria-label="Code PIN">{{ pinLetters.join(" ") }}</span>
        <button type="button" class="sm" @click="copyPin">Copier</button>
      </div>
      <p class="actions">
        <button type="button" class="sm" @click="openAllDebrid">Ouvrir AllDebrid</button>
      </p>
      <p class="wait-line">
        <span class="spin" aria-hidden="true" />
        <span>
          En attente de validation · expire dans <strong>{{ countdownLabel }}</strong>
        </span>
      </p>
      <p v-if="account?.pin_error" class="error">{{ account.pin_error }}</p>
      <button type="button" class="ghost" data-testid="alldebrid-cancel-pin" @click="cancelPin">Annuler</button>
    </template>

    <template v-else-if="showDisconnected">
      <p class="alldebrid-status row">
        <span class="status-pill off"><span class="dot" aria-hidden="true" />Non connecté</span>
      </p>
      <p class="hint">Connectez AllDebrid pour débloquer les liens. Le code PIN évite de coller la clé ici.</p>
      <button type="button" class="primary block" data-testid="alldebrid-start-pin" @click="startPin">
        Se connecter avec un code PIN
      </button>
      <button type="button" class="link-quiet" @click="manualOpen = true">Saisir la clé manuellement</button>
    </template>

    <div v-if="manualOpen && !account?.env_locked" class="manual-fold stack">
      <label>
        <span class="label">Clé API AllDebrid</span>
        <input v-model="manualKey" class="input filled" type="password" autocomplete="off" />
      </label>
      <p class="actions">
        <button type="button" class="primary" :disabled="manualSaving || !manualKey" @click="saveManual">
          Enregistrer la clé
        </button>
        <button type="button" @click="manualOpen = false">Annuler</button>
      </p>
    </div>

    <p v-if="actionError" class="error">{{ actionError }}</p>
  </div>
</template>
