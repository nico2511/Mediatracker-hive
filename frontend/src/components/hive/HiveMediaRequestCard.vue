<script setup lang="ts">
import { computed, ref } from "vue";
import { RouterLink, useRouter } from "vue-router";

import { api, userFacingApiError } from "../../api";
const props = defineProps<{
  mediaItemId: number;
  /** announced = pending|transfer on mine API */
  announced: boolean;
  promptAvailable: boolean;
  retracted?: boolean;
  busy?: boolean;
}>();

const emit = defineEmits<{ changed: [] }>();

const router = useRouter();
const error = ref("");
const localBusy = ref(false);

const showCard = computed(() => props.announced || props.promptAvailable || props.retracted);

const badge = computed(() => (props.announced ? "Demandé à la ruche" : ""));

const hint = computed(() => {
  if (props.announced) {
    return "Suivi en attente, annoncé anonymement. L’avancement figure aussi dans Mes demandes (la file de suivi).";
  }
  if (props.retracted) {
    return "Vous avez retiré la demande à la ruche. Rien n’est annoncé tant que vous ne confirmez pas.";
  }
  if (props.promptAvailable) {
    return "La recherche habituelle n’a rien trouvé. Vous pouvez annoncer ce suivi anonymement à la ruche.";
  }
  return "";
});

async function announce() {
  localBusy.value = true;
  error.value = "";
  try {
    await api("/api/v1/hive/requests/announce", {
      method: "POST",
      body: JSON.stringify({ media_item_id: props.mediaItemId }),
    });
    emit("changed");
  } catch (err) {
    error.value = userFacingApiError(err, "Annonce impossible.");
  } finally {
    localBusy.value = false;
  }
}

async function retract() {
  localBusy.value = true;
  error.value = "";
  try {
    await api("/api/v1/hive/requests/retract", {
      method: "POST",
      body: JSON.stringify({ media_item_id: props.mediaItemId }),
    });
    emit("changed");
  } catch (err) {
    error.value = userFacingApiError(err, "Retrait impossible.");
  } finally {
    localBusy.value = false;
  }
}

function openMine() {
  void router.push("/hive/demandes");
}
</script>

<template>
  <article
    v-if="showCard"
    class="panel hive-media-request-card"
    aria-live="polite"
    data-testid="hive-media-request-card"
  >
    <svg class="hive-media-request-ico" viewBox="0 0 24 24" width="20" height="20" aria-hidden="true">
      <path
        d="M12 2 18 6v6l-6 10-6-10V6Z"
        fill="none"
        stroke="currentColor"
        stroke-width="1.75"
        stroke-linecap="round"
        stroke-linejoin="round"
      />
    </svg>
    <div class="hive-media-request-main">
      <p class="hive-media-request-title">
        <span class="lbl hint">Hive</span>
        <span v-if="badge" class="pill honey">{{ badge }}</span>
      </p>
      <p class="hint hive-media-request-hint">{{ hint }}</p>
      <p v-if="error" class="error" role="alert">{{ error }}</p>
    </div>
    <div class="hive-media-request-actions">
      <button
        v-if="(promptAvailable || retracted) && !announced"
        type="button"
        class="btn primary"
        data-testid="hive-request-announce"
        :disabled="localBusy || busy"
        @click="announce"
      >
        Demander à la ruche
      </button>
      <template v-else-if="announced">
        <RouterLink class="btn" to="/hive/demandes" data-testid="hive-request-open-mine" @click.prevent="openMine">
          Voir mes demandes
        </RouterLink>
        <button
          type="button"
          class="btn linkish"
          data-testid="hive-request-retract"
          :disabled="localBusy || busy"
          @click="retract"
        >
          Ne plus demander à la ruche
        </button>
      </template>
    </div>
  </article>
</template>

<style scoped>
.hive-media-request-card {
  display: flex;
  flex-wrap: wrap;
  align-items: flex-start;
  gap: 0.65rem 1rem;
  padding: 0.75rem 1rem;
}

.hive-media-request-ico {
  color: var(--honey, #d9b46a);
  flex-shrink: 0;
}

.hive-media-request-main {
  flex: 1 1 12rem;
  min-width: 0;
}

.hive-media-request-title {
  margin: 0;
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 0.5rem;
}

.hive-media-request-hint {
  margin: 0.35rem 0 0;
}

.hive-media-request-actions {
  display: flex;
  flex-direction: column;
  align-items: stretch;
  gap: 0.45rem;
  min-width: min(100%, 14rem);
}

.btn.linkish {
  background: transparent;
  border: 0;
  color: var(--muted);
  text-decoration: underline;
  min-height: var(--target-min);
}

.pill.honey {
  background: color-mix(in srgb, var(--honey, #d9b46a) 22%, transparent);
  border: 1px solid color-mix(in srgb, var(--honey, #d9b46a) 55%, transparent);
  color: var(--text);
  font-size: 0.82rem;
  padding: 0.15rem 0.55rem;
  border-radius: 999px;
}
</style>