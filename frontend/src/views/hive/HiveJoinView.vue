<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { useRouter } from "vue-router";

import { api, userFacingApiError } from "../../api";
import { parseInvitationLink } from "../../hive/hiveJoinLink";
import {
  clearHiveJoinWait,
  readHiveJoinWait,
  writeHiveJoinWait,
  type HiveJoinWaitState,
} from "../../hive/hiveJoinSession";

const router = useRouter();

interface PreviewRow {
  ruche_id_hex?: string;
  member_count?: number;
  expires_at?: string;
}

interface BeginRow {
  control_code: string;
  pending_id: number;
  invitee_instance_id: string;
  creator_base_url: string;
  ruche_id_hex: string;
}

const link = ref("");
const preview = ref<PreviewRow | null>(null);
const wait = ref<HiveJoinWaitState | null>(null);
const error = ref("");
const note = ref("");
const busy = ref(false);
const step = ref<"paste" | "confirm" | "wait" | "done">("paste");

const displayRuche = computed(() => {
  const hex = preview.value?.ruche_id_hex || wait.value?.ruche_id_hex;
  return hex ? `${hex.slice(0, 8)}…` : "—";
});

function restoreSession() {
  const stored = readHiveJoinWait();
  if (stored) {
    wait.value = stored;
    step.value = "wait";
  }
}

onMounted(() => {
  restoreSession();
});

async function onPreview() {
  error.value = "";
  note.value = "";
  busy.value = true;
  try {
    preview.value = await api<PreviewRow>("/api/v1/hive/join/preview", {
      method: "POST",
      body: JSON.stringify({ link: link.value.trim() }),
    });
    step.value = "confirm";
  } catch (err) {
    error.value = userFacingApiError(err, "Lien d’invitation invalide.");
  } finally {
    busy.value = false;
  }
}

async function onJoin() {
  error.value = "";
  note.value = "";
  busy.value = true;
  try {
    const identity = await api<{ name: string | null; public_address: string | null }>("/api/v1/hive/identity");
    const inviteeName = identity.name?.trim();
    if (!inviteeName) {
      error.value = "Activez Hive avec un nom d’instance avant de rejoindre une ruche.";
      return;
    }
    const begun = await api<BeginRow>("/api/v1/hive/join/begin", {
      method: "POST",
      body: JSON.stringify({
        link: link.value.trim(),
        invitee_name: inviteeName,
        invitee_public_address: identity.public_address?.trim() || "",
      }),
    });
    const parsed = parseInvitationLink(link.value);
    const creatorId = parsed?.creator_id;
    if (!creatorId) {
      error.value = "Lien d’invitation incomplet (créateur absent).";
      return;
    }
    const session: HiveJoinWaitState = {
      ruche_id_hex: begun.ruche_id_hex,
      creator_id: creatorId,
      creator_base_url: begun.creator_base_url,
      control_code: begun.control_code,
    };
    writeHiveJoinWait(session);
    wait.value = session;
    step.value = "wait";
    note.value =
      "Demande envoyée au créateur. Communiquez-lui le code de contrôle ci-dessous pour qu’il puisse vous accepter.";
  } catch (err) {
    error.value = userFacingApiError(err, "Impossible de démarrer l’adhésion.");
  } finally {
    busy.value = false;
  }
}

async function onConfirmMembership() {
  const state = wait.value;
  if (!state) {
    return;
  }
  error.value = "";
  note.value = "";
  busy.value = true;
  try {
    await api("/api/v1/hive/join/confirm", {
      method: "POST",
      body: JSON.stringify({
        ruche_id_hex: state.ruche_id_hex,
        creator_base_url: state.creator_base_url,
        creator_instance_id: state.creator_id,
      }),
    });
    clearHiveJoinWait();
    wait.value = null;
    step.value = "done";
    note.value = "Adhésion confirmée. La ruche apparaît dans Réglages → Hive.";
  } catch (err) {
    error.value = userFacingApiError(
      err,
      "Le créateur n’a pas encore accepté votre demande, ou l’acceptation a échoué.",
    );
  } finally {
    busy.value = false;
  }
}

function onAbandon() {
  clearHiveJoinWait();
  wait.value = null;
  preview.value = null;
  step.value = "paste";
  link.value = "";
  error.value = "";
  note.value = "";
}

async function goSettings() {
  await router.push("/reglages/hive");
}
</script>

<template>
  <div class="hive-join zone card stack">
    <h2>Rejoindre avec un lien</h2>
    <p class="hint">Collez un lien <code>mtrhive:1:…</code> fourni par le créateur de la ruche.</p>

    <template v-if="step === 'wait' || step === 'done'">
      <p v-if="note" class="ok">{{ note }}</p>
      <p v-if="error" class="error" role="alert">{{ error }}</p>
      <div v-if="wait && step === 'wait'" class="hive-join-wait panel stack">
        <p><strong>Ruche :</strong> {{ displayRuche }}</p>
        <p class="hint">En attente d’approbation du créateur. Sans action de sa part, l’adhésion ne se fera pas.</p>
        <p class="hive-join-code-label label">Code de contrôle (à donner au créateur)</p>
        <p class="hive-join-code" data-testid="hive-control-code">{{ wait.control_code }}</p>
        <div class="row wrap">
          <button type="button" class="btn primary" :disabled="busy" data-testid="hive-confirm-join" @click="onConfirmMembership">
            Vérifier l’acceptation
          </button>
          <button type="button" class="btn" :disabled="busy" @click="onAbandon">Annuler la demande</button>
        </div>
      </div>
      <div v-else-if="step === 'done'" class="row wrap">
        <button type="button" class="btn primary" @click="goSettings">Ouvrir Réglages Hive</button>
        <button type="button" class="btn" @click="onAbandon">Nouvelle invitation</button>
      </div>
    </template>

    <template v-else>
      <label class="label" for="hive-invite-link">Lien d’invitation</label>
      <input
        id="hive-invite-link"
        v-model="link"
        class="input block hive-join-link"
        type="text"
        placeholder="mtrhive:1:…"
        autocomplete="off"
        autocorrect="off"
        autocapitalize="off"
        spellcheck="false"
        data-testid="hive-invite-link"
      />
      <p v-if="error" class="error" role="alert">{{ error }}</p>
      <p v-if="note" class="ok">{{ note }}</p>
      <div v-if="preview && step === 'confirm'" class="stack hive-join-preview">
        <p><strong>Ruche :</strong> {{ displayRuche }}</p>
        <p><strong>Membres :</strong> {{ preview.member_count ?? "—" }}</p>
        <p v-if="preview.expires_at" class="hint">Invitation valable jusqu’à {{ preview.expires_at }}</p>
      </div>
      <div class="row wrap">
        <button
          v-if="step === 'paste'"
          type="button"
          class="btn primary"
          :disabled="!link.trim() || busy"
          data-testid="hive-preview"
          @click="onPreview"
        >
          Vérifier
        </button>
        <template v-else>
          <button type="button" class="btn primary" :disabled="busy" data-testid="hive-begin" @click="onJoin">Continuer</button>
          <button type="button" class="btn" :disabled="busy" @click="onAbandon">Retour</button>
        </template>
      </div>
    </template>
  </div>
</template>

<style scoped>
.hive-join {
  max-width: 36rem;
  gap: 0.75rem;
}

.hive-join-link {
  overflow-x: auto;
  white-space: nowrap;
  font-family: ui-monospace, monospace;
  font-size: 0.88rem;
}

.hive-join-wait {
  gap: 0.65rem;
  padding: 0.85rem 1rem;
}

.hive-join-code {
  margin: 0;
  font-family: ui-monospace, monospace;
  font-size: 1.45rem;
  letter-spacing: 0.18em;
  font-weight: 700;
}

.hive-join-code-label {
  margin-bottom: -0.25rem;
}
</style>
