<script setup lang="ts">
import { computed, ref, watch } from "vue";

const props = withDefaults(
  defineProps<{
    open: boolean;
    count: number;
    title?: string;
    titles?: string[];
    titlePreviewMax?: number;
    /** When true, files stay on disk (no trash choice); confirm always sends `trash: false`. */
    filesOnDiskUntouched?: boolean;
  }>(),
  {
    titlePreviewMax: 4,
    filesOnDiskUntouched: false,
  },
);
const emit = defineEmits<{ cancel: []; confirm: [trash: boolean] }>();
const trash = ref(true);
const bulk = computed(() => props.count > 1);

const previewTitles = computed(() => {
  const rows = props.titles ?? (props.title && props.count === 1 ? [props.title] : []);
  if (!rows.length) {
    return { shown: [] as string[], rest: 0 };
  }
  const cap = Math.max(1, props.titlePreviewMax);
  if (rows.length <= cap) {
    return { shown: rows, rest: 0 };
  }
  return { shown: rows.slice(0, cap), rest: rows.length - cap };
});

function submit() {
  emit("confirm", props.filesOnDiskUntouched ? false : trash.value);
}

watch(
  () => props.open,
  (open) => {
    if (open) {
      trash.value = true;
    }
  },
);
</script>

<template>
  <div v-if="open" class="dialog-backdrop" role="presentation" @click.self="emit('cancel')">
    <form class="panel dialog" role="dialog" aria-labelledby="delete-fiche-title" @submit.prevent="submit">
      <h2 id="delete-fiche-title">Supprimer {{ bulk ? `${count} fiches` : "la fiche" }} ?</h2>
      <p v-if="title && count === 1 && !titles?.length"><strong>{{ title }}</strong></p>
      <ul v-else-if="previewTitles.shown.length" class="delete-fiche-titles">
        <li v-for="(line, index) in previewTitles.shown" :key="`${index}-${line}`">{{ line }}</li>
        <li v-if="previewTitles.rest" class="hint">et {{ previewTitles.rest }} autre{{ previewTitles.rest > 1 ? "s" : "" }}</li>
      </ul>
      <p class="hint">
        <template v-if="bulk">
          Les fiches, leurs épisodes, leurs liens et leur historique sont retirés. Les téléchargements en cours sont
          annulés. Le journal des renommages est conservé.
        </template>
        <template v-else>
          La fiche, ses épisodes, ses liens et son historique sont retirés. Les téléchargements en cours sont annulés.
          Le journal des renommages est conservé.
        </template>
        <template v-if="filesOnDiskUntouched">
          Les fichiers sur le disque ne sont pas touchés.
        </template>
      </p>
      <template v-if="!filesOnDiskUntouched">
        <p class="hint"><strong>Et les fichiers ?</strong></p>
        <label class="check">
          <input v-model="trash" type="radio" :value="true" />
          <span>Mettre les fichiers à la corbeille — restaurables depuis la corbeille. Rien n'est effacé définitivement.</span>
        </label>
        <label class="check">
          <input v-model="trash" type="radio" :value="false" />
          <span>Garder les fichiers sur le disque — ils restent rangés dans la bibliothèque, sans fiche MediaTracker.</span>
        </label>
      </template>
      <p class="actions">
        <button type="submit" class="danger">{{ bulk ? "Supprimer les fiches" : "Supprimer la fiche" }}</button>
        <button type="button" @click="emit('cancel')">Annuler</button>
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
