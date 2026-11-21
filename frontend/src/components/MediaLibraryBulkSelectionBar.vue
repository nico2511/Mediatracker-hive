<script setup lang="ts">
import { computed } from "vue";

import ActionMenuPopover from "./ActionMenuPopover.vue";

const props = defineProps<{
  variant: "top" | "mobile";
  selectedCount: number;
  visibleCount: number;
  hiveEnabled: boolean;
  hiveShareBusy: boolean;
  deleteBusy: boolean;
  serverActionsDisabled: boolean;
}>();

const emit = defineEmits<{
  share: [];
  unshare: [];
  selectAll: [];
  cancel: [];
  delete: [];
}>();

const barClass = computed(() =>
  props.variant === "top"
    ? "media-library-bulk-bar media-library-bulk-bar--top actions"
    : "media-library-bulk-bar media-library-bulk-bar--mobile actions",
);

const selectionDisabled = computed(
  () => !props.selectedCount || props.hiveShareBusy || props.deleteBusy || props.serverActionsDisabled,
);
</script>

<template>
  <div :class="barClass" role="toolbar" aria-label="Sélection des fiches">
    <span class="hint media-library-bulk-count">
      <span class="media-library-bulk-count-full">
        {{ selectedCount }} sélectionnée{{ selectedCount > 1 ? "s" : "" }}
      </span>
      <span class="media-library-bulk-count-short" aria-hidden="true">{{ selectedCount }} sél.</span>
    </span>

    <template v-if="hiveEnabled">
      <button
        type="button"
        class="primary media-library-bulk-share"
        :disabled="selectionDisabled"
        @click="emit('share')"
      >
        <span class="media-library-bulk-share-label-full">Partager sur Hive</span>
        <span class="media-library-bulk-share-label-short" aria-hidden="true">Partager</span>
      </button>
      <button
        v-if="variant === 'top'"
        type="button"
        :disabled="selectionDisabled"
        @click="emit('unshare')"
      >
        Ne plus partager
      </button>
    </template>

    <template v-if="variant === 'top'">
      <button type="button" :disabled="!visibleCount" @click="emit('selectAll')">Tout sélectionner</button>
      <button type="button" @click="emit('cancel')">Annuler</button>
      <button
        type="button"
        class="danger"
        :disabled="!selectedCount || deleteBusy || serverActionsDisabled"
        @click="emit('delete')"
      >
        Supprimer les fiches
      </button>
    </template>

    <template v-else>
      <button type="button" @click="emit('cancel')">Annuler</button>
      <ActionMenuPopover
        label="Plus"
        menu-aria-label="Actions de sélection"
        trigger-variant="toggle"
        :show-status-icon="false"
      >
        <button type="button" role="menuitem" :disabled="!visibleCount" @click="emit('selectAll')">
          Tout sélectionner
        </button>
        <button
          v-if="hiveEnabled"
          type="button"
          role="menuitem"
          :disabled="selectionDisabled"
          @click="emit('unshare')"
        >
          Ne plus partager
        </button>
        <button
          type="button"
          role="menuitem"
          class="danger"
          :disabled="!selectedCount || deleteBusy || serverActionsDisabled"
          @click="emit('delete')"
        >
          Supprimer les fiches
        </button>
      </ActionMenuPopover>
    </template>
  </div>
</template>