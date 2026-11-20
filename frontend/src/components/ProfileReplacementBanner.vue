<script setup lang="ts">
import { ref } from "vue";

import { ApiError, api, userFacingApiError } from "../api";

export interface ProfileReplacementProposal {
  id: number;
  principal_profile_id: number;
  principal_profile_name: string;
  repli_profile_id: number;
  repli_profile_name: string;
}

const props = defineProps<{
  itemId: number;
  proposal: ProfileReplacementProposal;
}>();

const emit = defineEmits<{
  changed: [];
}>();

const busy = ref(false);
const error = ref("");

async function replace() {
  if (busy.value) {
    return;
  }
  busy.value = true;
  error.value = "";
  try {
    await api(`/api/v1/items/${props.itemId}/profile-replacement/replace`, { method: "POST" });
    emit("changed");
  } catch (exc) {
    error.value = exc instanceof ApiError ? userFacingApiError(exc) : "Remplacement impossible.";
  } finally {
    busy.value = false;
  }
}

async function dismiss() {
  if (busy.value) {
    return;
  }
  busy.value = true;
  error.value = "";
  try {
    await api(`/api/v1/items/${props.itemId}/profile-replacement/dismiss`, { method: "POST" });
    emit("changed");
  } catch (exc) {
    error.value = exc instanceof ApiError ? userFacingApiError(exc) : "Action impossible.";
  } finally {
    busy.value = false;
  }
}
</script>

<template>
  <section class="profile-repl-banner panel" data-testid="profile-replacement-banner">
    <h3 class="profile-repl-title">Version du profil principal disponible</h3>
    <p class="profile-repl-text">
      Ce fichier a été téléchargé via le profil de repli {{ proposal.repli_profile_name }}. Une version du profil
      principal {{ proposal.principal_profile_name }} est maintenant disponible.
    </p>
    <p class="hint profile-repl-foot">L'ancien fichier part à la corbeille après remplacement.</p>
  <p v-if="error" class="error profile-repl-error" role="alert">{{ error }}</p>
    <div class="profile-repl-actions">
      <button type="button" class="primary" :disabled="busy" @click="replace">Remplacer</button>
      <button type="button" class="secondary" :disabled="busy" @click="dismiss">Ignorer</button>
    </div>
  </section>
</template>