<script setup lang="ts">
export type HomeFilterKey = "films" | "series" | "upcoming";

const props = defineProps<{
  active: HomeFilterKey[];
}>();

const emit = defineEmits<{
  toggle: [HomeFilterKey];
}>();

const chips: { key: HomeFilterKey; label: string }[] = [
  { key: "films", label: "Films" },
  { key: "series", label: "Séries" },
  { key: "upcoming", label: "Sorties à venir" },
];

function isOn(key: HomeFilterKey): boolean {
  return props.active.includes(key);
}
</script>

<template>
  <div class="home-filter-chips" role="group" aria-label="Filtrer l'accueil">
    <button
      v-for="chip in chips"
      :key="chip.key"
      type="button"
      class="home-filter-chip"
      :class="{ 'is-active': isOn(chip.key) }"
      :aria-pressed="isOn(chip.key)"
      @click="emit('toggle', chip.key)"
    >
      {{ chip.label }}
    </button>
  </div>
</template>
