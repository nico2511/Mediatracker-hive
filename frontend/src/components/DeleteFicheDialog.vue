<script setup lang="ts">
import { computed, ref, watch } from "vue";

const props = defineProps<{ open: boolean; count: number; title?: string }>();
const emit = defineEmits<{ cancel: []; confirm: [trash: boolean] }>();
const trash = ref(true);
const bulk = computed(() => props.count > 1);

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
    <form class="panel dialog" role="dialog" aria-labelledby="delete-fiche-title" @submit.prevent="emit('confirm', trash)">
      <h2 id="delete-fiche-title">Supprimer {{ bulk ? `${count} fiches` : "la fiche" }} ?</h2>
      <p v-if="title && count === 1"><strong>{{ title }}</strong></p>
      <p class="hint">
        <template v-if="bulk">
          Les fiches, leurs épisodes, leurs liens et leur historique sont retirés. Les téléchargements en cours sont
          annulés. Le journal des renommages est conservé.
        </template>
        <template v-else>
          La fiche, ses épisodes, ses liens et son historique sont retirés. Les téléchargements en cours sont annulés.
          Le journal des renommages est conservé.
        </template>
      </p>
      <p class="hint"><strong>Et les fichiers ?</strong></p>
      <label class="check">
        <input v-model="trash" type="radio" :value="true" />
        <span>Mettre les fichiers à la corbeille — restaurables depuis la corbeille. Rien n'est effacé définitivement.</span>
      </label>
      <label class="check">
        <input v-model="trash" type="radio" :value="false" />
        <span>Garder les fichiers sur le disque — ils restent rangés dans la bibliothèque, sans fiche MediaTracker.</span>
      </label>
      <p class="actions">
        <button type="submit" class="danger">{{ bulk ? "Supprimer les fiches" : "Supprimer la fiche" }}</button>
        <button type="button" @click="emit('cancel')">Annuler</button>
      </p>
    </form>
  </div>
</template>
