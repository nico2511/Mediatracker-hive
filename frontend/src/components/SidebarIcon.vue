<script setup lang="ts">
import { computed } from "vue";

const props = defineProps<{ name: string }>();

const icons: Record<string, string[]> = {
  home: ["M4 10 12 3l8 7", "M6 9v10h12V9"],
  follow: ["M12 5v14", "M5 12h14"],
  media: ["M8 6h13", "M8 12h13", "M8 18h13", "M3 6h.01", "M3 12h.01", "M3 18h.01"],
  star: ["m12 3 2.2 4.5 5 .7-3.6 3.4.9 5-4.5-2.4-4.5 2.4.9-5L4.8 8.2l5-.7Z"],
  download: ["M12 3v12", "m7 10 5 5 5-5", "M5 21h14"],
  library: ["M4 20h16", "M6 20V6a2 2 0 0 1 2-2h8a2 2 0 0 1 2 2v14"],
  journal: ["M8 6h8", "M8 10h8", "M8 14h5", "M6 4h12a2 2 0 0 1 2 2v14l-2-1-2 1-2-1-2 1-2-1-2 1-2-1V6a2 2 0 0 1 2-2Z"],
  jellyfin: ["M4 8h16v11H4z", "M9 8V5h6v3"],
  settings: ["M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6Z", "M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.2a1.7 1.7 0 0 0-1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.2a1.7 1.7 0 0 0 1.5-1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3 1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.2a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8 1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.2a1.7 1.7 0 0 0-1.5 1Z"],
  health: ["M12 20s-7-4.4-7-10a4 4 0 0 1 7-2 4 4 0 0 1 7 2c0 5.6-7 10-7 10Z"],
  hive: ["M12 2 18 6v6l-6 10-6-10V6Z", "M12 8v8"],
  logout: ["M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4", "M16 17l5-5-5-5", "M21 12H9"],
  collapse: ["M9 6 3 12l6 6", "M21 12H10"],
  expand: ["M15 6 21 12l-6 6", "M3 12h11"],
};

const segments = computed(() => icons[props.name] || icons.home);
</script>

<template>
  <svg class="sidebar-icon" viewBox="0 0 24 24" aria-hidden="true" focusable="false">
    <path
      v-for="(segment, index) in segments"
      :key="index"
      :d="segment"
      fill="none"
      stroke="currentColor"
      stroke-width="1.75"
      stroke-linecap="round"
      stroke-linejoin="round"
    />
  </svg>
</template>
