<script setup lang="ts">
import { computed, ref } from "vue";

import { api, userFacingApiError } from "../../api";
import { hiveRucheConfirmName } from "../../hive/hiveRucheLabel";

const props = withDefaults(
  defineProps<{
    rucheIdHex: string;
    layout?: "zone" | "inline";
  }>(),
  { layout: "zone" },
);

const emit = defineEmits<{
  success: [];
  error: [message: string];
}>();

const catalogResetDialog = ref(false);
const catalogResetConfirm = ref("");
const catalogResetBusy = ref(false);

const rucheConfirmLabel = computed(() => hiveRucheConfirmName(props.rucheIdHex));

const catalogResetConfirmOk = computed(
  () => catalogResetConfirm.value.trim() === rucheConfirmLabel.value,
);

const dialogTitleId = computed(() => `hive-catalog-reset-dialog-title-${props.rucheIdHex.slice(0, 8)}`);
const zoneTitleId = computed(() => `hive-catalog-reset-title-${props.rucheIdHex.slice(0, 8)}`);

function openCatalogResetDialog() {
  catalogResetConfirm.value = "";
  catalogResetDialog.value = true;
}

async function submitCatalogReset() {
  if (!catalogResetConfirmOk.value) {
    return;
  }
  catalogResetBusy.value = true;
  try {
    await api(`/api/v1/hive/ruches/${props.rucheIdHex}/catalog-reset`, {
      method: "POST",
      body: JSON.stringify({ confirm_name: catalogResetConfirm.value.trim() }),
    });
    catalogResetDialog.value = false;
    catalogResetConfirm.value = "";
    emit("success");
  } catch (err) {
    emit("error", userFacingApiError(err, "Réinitialisation impossible."));
  } finally {
    catalogResetBusy.value = false;
  }
}
</script>

<template>
  <section
    v-if="layout === 'zone'"
    class="hive-block panel stack hive-danger-zone"
    :aria-labelledby="zoneTitleId"
    data-testid="hive-catalog-reset-zone"
  >
    <h3 :id="zoneTitleId" class="settings-subsection-title">Zone de danger</h3>
    <p class="hint">
      Efface le catalogue et les signaux de la ruche sur toutes les instances. Vos fichiers, fiches médias et membres ne
      sont pas touchés.
    </p>
    <button
      type="button"
      class="btn danger"
      data-testid="hive-catalog-reset-open"
      @click="openCatalogResetDialog"
    >
      Réinitialiser le catalogue de la ruche
    </button>
  </section>
  <button
    v-else
    type="button"
    class="btn btn-sm danger"
    data-testid="hive-catalog-reset-open"
    @click="openCatalogResetDialog"
  >
    Réinitialiser le catalogue
  </button>

  <Teleport to="body">
    <div
      v-if="catalogResetDialog"
      class="hive-catalog-reset-backdrop"
      role="presentation"
      data-testid="hive-catalog-reset-dialog"
      @click.self="catalogResetDialog = false"
    >
      <div
        class="hive-catalog-reset-dialog panel stack"
        role="dialog"
        aria-modal="true"
        :aria-labelledby="dialogTitleId"
      >
        <h3 :id="dialogTitleId">Réinitialiser le catalogue</h3>
        <ul class="hive-catalog-reset-lists">
          <li>
            <strong>Effacé</strong> : le catalogue et les signaux de la ruche, sur toutes les instances.
          </li>
          <li>
            <strong>Conservé</strong> : vos fichiers, vos fiches médias, les membres.
          </li>
        </ul>
        <label class="hive-field">
          <span class="label">Tapez « {{ rucheConfirmLabel }} » pour confirmer</span>
          <input
            v-model="catalogResetConfirm"
            class="input"
            type="text"
            :placeholder="rucheConfirmLabel"
            autocomplete="off"
            data-testid="hive-catalog-reset-input"
          />
        </label>
        <div class="hive-catalog-reset-actions stack">
          <button
            type="button"
            class="btn danger"
            :disabled="!catalogResetConfirmOk || catalogResetBusy"
            data-testid="hive-catalog-reset-submit"
            @click="submitCatalogReset"
          >
            Réinitialiser le catalogue
          </button>
          <button
            type="button"
            class="btn"
            :disabled="catalogResetBusy"
            data-testid="hive-catalog-reset-cancel"
            @click="catalogResetDialog = false"
          >
            Annuler
          </button>
        </div>
      </div>
    </div>
  </Teleport>
</template>

<style scoped>
.hive-danger-zone {
  border-color: var(--danger);
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

.hive-block.panel {
  gap: 0.75rem;
  padding: 1rem 1.15rem;
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  background: var(--card);
}

.hive-catalog-reset-backdrop {
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

.hive-catalog-reset-dialog {
  width: min(100%, 26rem);
  max-height: min(90vh, 100%);
  overflow: auto;
  padding: 1.1rem 1.2rem;
  gap: 0.85rem;
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  background: var(--card);
  box-shadow: 0 12px 40px color-mix(in srgb, var(--bg) 50%, transparent);
}

.hive-catalog-reset-lists {
  margin: 0;
  padding-left: 1.1rem;
  font-size: 0.92rem;
}

.hive-catalog-reset-actions {
  gap: 0.5rem;
}

.hive-catalog-reset-actions .btn {
  width: 100%;
}
</style>