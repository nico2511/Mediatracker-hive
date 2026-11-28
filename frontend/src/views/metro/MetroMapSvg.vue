<script setup lang="ts">
import { computed } from "vue";

import { edgeCurve, LINE_BACKBONES } from "../../metro/geometry";
import type { EdgeKind, MetroGraphBody, MetroGraphEdge, MetroGraphStation, MetroLevel } from "../../metro/types";

const props = defineProps<{
  graph: MetroGraphBody;
  level: MetroLevel;
  litEdges: string[];
  selectedStationId: string | null;
  selectedEdgeId: string | null;
  dimOthers: boolean;
  edgeHits: Record<string, number>;
  level1Edges: { from: string; to: string; count: number; count_label: string }[];
}>();

const emit = defineEmits<{
  selectStation: [id: string];
  selectEdge: [id: string];
  selectLine: [lineName: string];
}>();

const stationById = computed(() => {
  const map = new Map<string, MetroGraphStation>();
  for (const row of props.graph.stations) {
    map.set(row.id, row);
  }
  return map;
});

function strokeForKind(kind: EdgeKind, lit: boolean): { stroke: string; width: number; dash?: string; marker?: string } {
  if (lit) {
    return { stroke: "#f4f1ea", width: 3, marker: "lit" };
  }
  if (kind === "alimente") {
    return { stroke: "#9aa19a", width: 1.3, dash: "2 4" };
  }
  if (kind === "attend") {
    return { stroke: "#cfd2d0", width: 4.4, dash: "0" };
  }
  if (kind === "bloque") {
    return { stroke: "#d08a80", width: 1.6, marker: "block" };
  }
  return { stroke: "#cfd2d0", width: 1.5, marker: "arrow" };
}

function drawEdge(edge: MetroGraphEdge) {
  const from = stationById.value.get(edge.origin);
  const to = stationById.value.get(edge.dest);
  if (!from || !to) {
    return null;
  }
  const lit = props.litEdges.includes(edge.id);
  const attenuate = props.dimOthers && !lit && props.selectedEdgeId && props.selectedEdgeId !== edge.id;
  const style = strokeForKind(edge.kind, lit);
  return {
    id: edge.id,
    d: edgeCurve(from, to),
    ...style,
    opacity: attenuate ? 0.22 : edge.kind === "alimente" ? 0.38 : 0.55,
    untraceable: edge.untraceable,
    mid: { x: (from.x + to.x) / 2, y: (from.y + to.y) / 2 },
  };
}

const edges = computed(() => props.graph.edges.map(drawEdge).filter(Boolean));
</script>

<template>
  <svg
    class="metro-map-svg"
    :viewBox="`0 0 ${graph.view_box.width} ${graph.view_box.height}`"
    role="img"
    aria-label="Plan du réseau"
  >
    <defs>
      <marker id="metro-arrow" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
        <path d="M0,0 L8,4 L0,8 z" fill="#cfd2d0" />
      </marker>
      <marker id="metro-lit" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
        <path d="M0,0 L8,4 L0,8 z" fill="#f4f1ea" />
      </marker>
      <marker id="metro-block" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="8" markerHeight="8" orient="auto">
        <path d="M9,0 L9,10" stroke="#d08a80" stroke-width="2.4" />
      </marker>
    </defs>
    <template v-if="level === 1">
      <g v-for="row in level1Edges" :key="`${row.from}-${row.to}`">
        <line
          :x1="graph.lines.find((l) => l.name === row.from)?.x ?? 0"
          :y1="graph.lines.find((l) => l.name === row.from)?.y ?? 0"
          :x2="graph.lines.find((l) => l.name === row.to)?.x ?? 0"
          :y2="graph.lines.find((l) => l.name === row.to)?.y ?? 0"
          stroke="#cfd2d0"
          :stroke-width="Math.min(14, 2 + Number(row.count) / 8)"
          opacity="0.75"
        />
      </g>
    </template>
    <template v-if="level >= 2">
      <polyline
        v-for="(row, index) in LINE_BACKBONES"
        :key="index"
        :points="row.points"
        fill="none"
        :stroke="row.color"
        stroke-width="7"
        stroke-linecap="round"
        stroke-linejoin="round"
        :stroke-dasharray="row.dashed ? '12 7' : undefined"
      />
      <circle
        :cx="graph.ruche_ring.cx"
        :cy="graph.ruche_ring.cy"
        :r="graph.ruche_ring.r"
        fill="none"
        stroke="#C9910D"
        stroke-width="7"
      />
      <g opacity="0.9">
        <circle :cx="graph.peer_off_plan.x" :cy="graph.peer_off_plan.y" r="7" fill="none" stroke="#C9910D" stroke-width="2" stroke-dasharray="3 2" />
        <text :x="graph.peer_off_plan.x" :y="graph.peer_off_plan.y + 24" text-anchor="end" fill="#9aa19a" font-size="13">Pair (hors plan)</text>
      </g>
    </template>
    <g v-for="edge in edges" :key="edge!.id">
      <path
        :d="edge!.d"
        fill="none"
        :stroke="edge!.stroke"
        :stroke-width="edge!.width"
        :stroke-dasharray="edge!.dash"
        :opacity="edge!.opacity"
        :marker-end="edge!.marker === 'lit' ? 'url(#metro-lit)' : edge!.marker === 'block' ? 'url(#metro-block)' : edge!.marker === 'arrow' ? 'url(#metro-arrow)' : undefined"
        class="metro-edge-hit"
        :data-testid="`metro-edge-${edge!.id}`"
        @click.stop="emit('selectEdge', edge!.id)"
      />
      <g v-if="edge!.untraceable" :transform="`translate(${edge!.mid.x - 7}, ${edge!.mid.y - 7})`">
        <circle r="7" fill="#16191b" stroke="#7d8388" />
        <text x="0" y="4" text-anchor="middle" fill="#b5bab6" font-size="10" font-weight="700">?</text>
      </g>
    </g>
    <g
      v-if="level >= 2"
      v-for="station in graph.stations"
      :key="station.id"
      class="metro-station"
      :class="{ 'metro-station--selected': selectedStationId === station.id }"
      @click.stop="emit('selectStation', station.id)"
    >
      <circle
        :cx="station.x"
        :cy="station.y"
        :r="station.id === 'RNG5' ? 17 : station.id === 'DL1' || station.id === 'ADB1' ? 13 : 8"
        fill="none"
        :stroke="station.id === selectedStationId ? '#c4b8a5' : station.color"
        :stroke-width="station.id === selectedStationId ? 3 : station.id === 'RNG5' ? 2 : 3"
        :stroke-dasharray="station.id === 'RUC2' || station.id === 'JF4' ? '3 2.5' : undefined"
      />
      <circle
        :cx="station.x"
        :cy="station.y"
        :r="station.id === 'RNG5' || station.id === 'DL1' ? 8 : 6"
        fill="#e7e6e3"
        :stroke="station.id === 'RNG5' || station.id === 'DL1' ? '#141618' : station.color"
        stroke-width="3"
      />
      <text
        :x="station.x + (station.label_dx ?? 0)"
        :y="station.y + (station.label_dy ?? -18)"
        :text-anchor="station.label_anchor ?? 'middle'"
        fill="#e7e6e3"
        :font-size="station.label_size ?? 12"
        stroke="#16191b"
        stroke-width="4"
        paint-order="stroke"
      >
        {{ station.label }}
      </text>
    </g>
    <template v-if="level === 1">
      <g
        v-for="line in graph.lines"
        :key="line.name"
        class="metro-line"
        role="button"
        tabindex="0"
        :data-testid="`metro-line-${line.number}`"
        @click.stop="emit('selectLine', line.name)"
        @keydown.enter.prevent="emit('selectLine', line.name)"
      >
        <circle :cx="line.x" :cy="line.y" r="28" :fill="line.color" opacity="0.95" />
        <text :x="line.x" :y="line.y + 5" text-anchor="middle" font-size="15" font-weight="700" :fill="line.number === 1 || line.number === 3 || line.number === 8 || line.number === 11 ? '#141618' : '#fff'">
          {{ line.number }}
        </text>
      </g>
    </template>
  </svg>
</template>