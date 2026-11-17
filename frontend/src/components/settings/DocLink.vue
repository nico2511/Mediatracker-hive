<script setup lang="ts">
defineOptions({ inheritAttrs: false });

defineProps<{
  href: string;
  label: string;
}>();
</script>

<template>
  <a :href="href" target="_blank" rel="noreferrer noopener" class="doc-link" v-bind="$attrs">{{ label }} ↗</a>
</template>