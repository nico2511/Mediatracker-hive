<script setup lang="ts">
import { computed, onMounted, onUnmounted, watch } from "vue";

const props = defineProps<{
  open: boolean;
  youtubeKey: string;
  subtitle: string | null;
}>();

const emit = defineEmits<{ close: [] }>();

const embedUrl = computed(
  () => `https://www.youtube-nocookie.com/embed/${encodeURIComponent(props.youtubeKey)}`,
);

function onKeydown(event: KeyboardEvent) {
  if (event.key === "Escape" && props.open) {
    emit("close");
  }
}

onMounted(() => window.addEventListener("keydown", onKeydown));
onUnmounted(() => window.removeEventListener("keydown", onKeydown));

watch(
  () => props.open,
  (isOpen) => {
    document.body.classList.toggle("trailer-player-open", isOpen);
  },
  { immediate: true },
);
onUnmounted(() => document.body.classList.remove("trailer-player-open"));
</script>

<template>
  <Teleport to="body">
    <h2
      v-if="open && subtitle"
      id="trailer-player-subtitle"
      class="trailer-player-subtitle-visually-hidden"
    >
      {{ subtitle }}
    </h2>
    <div v-if="open" class="trailer-player-root">
      <div class="trailer-player-backdrop" role="presentation" @click="emit('close')" />
      <div
        class="trailer-player-panel panel"
        role="dialog"
        aria-modal="true"
        aria-labelledby="trailer-player-title"
        :aria-describedby="subtitle ? 'trailer-player-subtitle' : undefined"
      >
        <header class="trailer-player-head">
          <div class="trailer-player-titles">
            <h2 id="trailer-player-title">Bande-annonce</h2>
            <p v-if="subtitle" class="hint trailer-player-subtitle">{{ subtitle }}</p>
          </div>
          <button type="button" class="icon-button trailer-player-close" aria-label="Fermer" @click="emit('close')">
            ×
          </button>
        </header>
        <div class="trailer-player-frame-wrap">
          <iframe
            class="trailer-player-frame"
            :src="embedUrl"
            title="Bande-annonce"
            referrerpolicy="strict-origin-when-cross-origin"
            allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture"
            allowfullscreen
          />
        </div>
      </div>
    </div>
  </Teleport>
</template>