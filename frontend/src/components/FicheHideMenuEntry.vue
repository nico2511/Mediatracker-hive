<script setup lang="ts">
import StatusIcon from "./StatusIcon.vue";

defineEmits<{
  hide: [];
}>();
</script>

<template>
  <div class="action-menu-hide-block" role="none">
    <button type="button" class="action-menu-item action-menu-hide-btn" role="menuitem" @click="$emit('hide')">
      <StatusIcon name="hide" label="Masquer la fiche" />
      <span class="action-menu-hide-copy">
        <span class="action-menu-hide-label">Masquer la fiche</span>
        <span class="hint action-menu-hide-hint">
          Retirée de Médias, de l'Accueil et des suggestions. Rien n'est supprimé.
        </span>
      </span>
    </button>
  </div>
</template>

<style scoped>
.action-menu-hide-block {
  margin: 0.2rem 0.35rem;
  padding: 0.15rem;
  border-radius: 8px;
  background: rgba(231, 230, 227, 0.05);
}

.action-menu-hide-btn {
  flex-direction: row;
  align-items: flex-start;
}

.action-menu-hide-copy {
  display: flex;
  flex-direction: column;
  gap: 0.2rem;
  min-width: 0;
}

.action-menu-hide-label {
  font-weight: 600;
}

.action-menu-hide-hint {
  font-size: 0.8125rem;
  line-height: 1.35;
  font-weight: 400;
}
</style>