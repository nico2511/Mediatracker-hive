<script setup lang="ts">
import { computed } from "vue";

import {
  formatAgeFr,
  formatBpsPerSecond,
  groupPeersByRuche,
  loadBarPercent,
  loadRatioLabel,
  peerInstanceLabel,
  peerReachability,
  peerReachabilityLabel,
  summarizePeerLoad,
  type HivePeerLoadResponse,
} from "../hive/hivePeerLoad";

const props = defineProps<{
  payload: HivePeerLoadResponse | null;
  error?: string;
}>();

const localId = computed(() => props.payload?.self?.instance_id ?? null);

const summary = computed(() =>
  summarizePeerLoad(props.payload?.self ?? null, props.payload?.items ?? [], localId.value),
);

const groups = computed(() => groupPeersByRuche(props.payload?.items ?? [], localId.value));

const hasPeers = computed(() => groups.value.some((g) => g.peers.length > 0));
</script>

<template>
  <article class="panel health-hive-ruche" data-testid="health-hive-ruche">
    <header class="health-hive-ruche-head">
      <span class="health-hive-ruche-icon" aria-hidden="true">⬡</span>
      <div>
        <h3>Ruche</h3>
        <p class="hint">Charge des pairs, d'après les derniers échanges.</p>
      </div>
    </header>

    <p v-if="error" class="error">{{ error }}</p>

    <template v-else-if="payload">
      <div class="health-hive-summary">
        <div class="health-hive-summary-col">
          <p class="health-hive-summary-value">{{ summary.sendsLabel }}</p>
          <p class="hint">Mes envois</p>
          <p class="health-hive-summary-sub health-hive-summary-sub--short hint">en cours</p>
          <p class="health-hive-summary-sub health-hive-summary-sub--long hint">transferts sortants en cours</p>
        </div>
        <div class="health-hive-summary-col">
          <p class="health-hive-summary-value">{{ summary.outboundLabel }}</p>
          <p class="hint">Débit sortant</p>
          <p class="health-hive-summary-sub hint">actuel</p>
        </div>
        <div class="health-hive-summary-col">
          <p class="health-hive-summary-value health-hive-pair-counts">
            <span class="health-hive-count-ok">{{ summary.reachable }}</span>
            <span aria-hidden="true"> / </span>
            <span class="health-hive-count-busy">{{ summary.busy }}</span>
            <span aria-hidden="true"> / </span>
            <span class="health-hive-count-unknown">{{ summary.unknown }}</span>
          </p>
          <p class="hint">Pairs</p>
          <p class="health-hive-summary-sub hint">joignables · occupés · inconnus</p>
        </div>
      </div>

      <p v-if="!hasPeers" class="hint health-hive-empty">Aucun pair connu pour le moment.</p>

      <div v-else class="health-hive-groups">
        <section
          v-for="group in groups"
          :key="group.rucheIdHex"
          class="health-hive-group"
          :data-testid="`hive-group-${group.rucheIdHex.slice(0, 8)}`"
        >
          <h4 class="health-hive-group-title">{{ group.label }}</h4>
          <ul class="health-hive-peer-list">
            <li
              v-for="peer in group.peers"
              :key="`${group.rucheIdHex}-${peer.origin_instance_id}`"
              class="health-hive-peer"
              :class="{ 'health-hive-peer-stale': peer.stale }"
            >
              <div class="health-hive-peer-top">
                <strong>{{ peerInstanceLabel(peer.origin_instance_id, localId) }}</strong>
                <span
                  class="health-hive-peer-badge"
                  :class="`health-hive-peer-badge--${peerReachability(peer)}`"
                >
                  {{ peerReachabilityLabel(peerReachability(peer)) }}
                </span>
              </div>
              <div class="health-hive-peer-load">
                <span class="health-hive-peer-ratio">{{ loadRatioLabel(peer.out_active, peer.parallel_limit, peer.stale) }}</span>
                <div class="health-hive-peer-bar" role="progressbar" :aria-valuenow="loadBarPercent(peer.out_active, peer.parallel_limit)" aria-valuemin="0" aria-valuemax="100">
                  <span
                    class="health-hive-peer-bar-fill"
                    :style="{
                      width: peer.stale ? '0%' : `${loadBarPercent(peer.out_active, peer.parallel_limit)}%`,
                    }"
                  />
                </div>
                <span class="hint health-hive-peer-speed">
                  {{ formatBpsPerSecond(peer.send_avail_bps) }} dispo
                  <span class="health-hive-peer-cap">· plafond {{ formatBpsPerSecond(peer.send_cap_bps) }}</span>
                </span>
              </div>
              <p class="hint health-hive-peer-age">
                {{ formatAgeFr(peer.received_at) }}
                <span v-if="peer.stale" class="health-hive-stale-label">périmé</span>
              </p>
            </li>
          </ul>
        </section>
      </div>
    </template>
  </article>
</template>