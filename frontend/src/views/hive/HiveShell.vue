<script setup lang="ts">
import { RouterLink } from "vue-router";
</script>

<template>
  <div class="hive-shell zone">
    <header class="hive-head row sp">
      <h1>Hive</h1>
      <RouterLink class="btn btn-sm" to="/reglages/hive">Réglages Hive</RouterLink>
    </header>
    <RouterView />
  </div>
</template>

<style scoped>
.hive-head {
  margin-bottom: 1rem;
}
</style>
