<script setup lang="ts">
import { computed } from "vue";

const props = withDefaults(
  defineProps<{
    open: boolean;
    count: number;
    titles?: string[];
    titlePreviewMax?: number;
    busy?: boolean;
  }>(),
  {
    titlePreviewMax: 12,
    busy: false,
  },
);
const emit = defineEmits<{ cancel: []; confirm: [] }>();

const previewTitles = computed(() => {
  const rows = props.titles ?? [];
  if (!rows.length) {
    return { shown: [] as string[], rest: 0 };
  }
  const cap = Math.max(1, props.titlePreviewMax);
  if (rows.length <= cap) {
    return { shown: rows, rest: 0 };
  }
  return { shown: rows.slice(0, cap), rest: rows.length - cap };
});
</script>

<template>
  <div v-if="open" class="dialog-backdrop" role="presentation" @click.self="emit('cancel')">
    <form class="panel dialog" role="dialog" aria-labelledby="hive-bulk-share-title" @submit.prevent="emit('confirm')">
      <h2 id="hive-bulk-share-title">Partager sur Hive ?</h2>
      <ul v-if="previewTitles.shown.length" class="delete-fiche-titles">
        <li v-for="(line, index) in previewTitles.shown" :key="`${index}-${line}`">{{ line }}</li>
        <li v-if="previewTitles.rest" class="hint">
          et {{ previewTitles.rest }} autre{{ previewTitles.rest > 1 ? "s" : "" }}
        </li>
      </ul>
      <p class="hint">
        Une série ne partage que les épisodes présents sur disque. Les titres sans fichier seront ignorés.
      </p>
      <p class="actions">
        <button type="button" :disabled="busy" @click="emit('cancel')">Annuler</button>
        <button type="submit" class="primary" :disabled="busy || !count">Partager</button>
      </p>
    </form>
  </div>
</template>

<style scoped>
.delete-fiche-titles {
  margin: 0.35rem 0 0.5rem;
  padding-left: 1.1rem;
}
.delete-fiche-titles li {
  margin: 0.15rem 0;
}
</style>
