<script setup lang="ts">
import { computed } from "vue";
import { useRouter } from "vue-router";

import { formatParisClock } from "../time";
import { useConnectivity } from "../pwa/connectivity";

const router = useRouter();
const {
  mode,
  lastProbeAt,
  backOnlineVisible,
  hideBackOnlineBanner,
  retryConnection,
  queueCount,
} = useConnectivity();

const clock = computed(() => {
  const stamp = lastProbeAt.value;
  return stamp ? formatParisClock(stamp) : formatParisClock(new Date().toISOString());
});

const showQueueLink = computed(() => mode.value !== "online" || queueCount.value > 0);

async function onRetry() {
  await retryConnection();
}

function goQueue() {
  void router.push({ path: "/suivre", query: { file: "1" } });
}
</script>

<template>
  <div v-if="backOnlineVisible && mode === 'online'" class="netbar netbar-back" role="status">
    <span class="netbar-key">✓ De nouveau en ligne</span>
    <span class="netbar-muted">{{ clock }}</span>
    <button type="button" class="netbar-action link-button" @click="hideBackOnlineBanner">Masquer</button>
  </div>
  <div v-else-if="mode === 'offline'" class="netbar netbar-off" role="status">
    <span class="netbar-key">Hors ligne</span>
    <span class="netbar-muted">{{ clock }}</span>
    <button v-if="showQueueLink" type="button" class="netbar-action link-button" @click="goQueue">
      Mettre en file
    </button>
  </div>
  <div v-else-if="mode === 'server_down'" class="netbar netbar-down" role="status">
    <span class="netbar-key">Serveur injoignable</span>
    <span class="netbar-muted">{{ clock }}</span>
    <button type="button" class="netbar-action link-button" @click="onRetry">Réessayer</button>
  </div>
</template>
