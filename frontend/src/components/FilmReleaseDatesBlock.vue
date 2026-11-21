<script setup lang="ts">
import { computed } from "vue";

import {
  filmReleaseDisplayRegions,
  filmReleaseRowLabel,
  filmSearchFooterParts,
  formatFilmReleaseShort,
  highlightRegionForSource,
  regionLineLabel,
  type ReleaseByRegion,
  type ReleaseKind,
} from "../fiche/filmReleaseDates";

const props = defineProps<{
  releaseByRegion: ReleaseByRegion | null | undefined;
  catalogRegion: string;
  searchFrom: string | null | undefined;
  availabilitySource: string | null | undefined;
}>();

const kinds: ReleaseKind[] = ["theatrical", "digital", "physical"];

const regions = computed(() => filmReleaseDisplayRegions(props.catalogRegion));

const highlight = computed(() => highlightRegionForSource(props.availabilitySource, props.catalogRegion));

const footerParts = computed(() =>
  filmSearchFooterParts(props.availabilitySource, props.searchFrom, props.catalogRegion),
);

const hasAny = computed(() => {
  if (!props.releaseByRegion) {
    return false;
  }
  return regions.value.some((code) =>
    kinds.some((kind) => props.releaseByRegion?.[code]?.[kind]),
  );
});

function rawDate(region: string, kind: ReleaseKind): string | null | undefined {
  return props.releaseByRegion?.[region]?.[kind];
}

function dateHighlighted(region: string, kind: ReleaseKind): boolean {
  const h = highlight.value;
  if (!h.kind || !h.region) {
    return false;
  }
  if (h.kind !== kind || h.region !== region) {
    return false;
  }
  const raw = rawDate(region, kind);
  if (!raw || !props.searchFrom) {
    return false;
  }
  return raw.slice(0, 10) === props.searchFrom.slice(0, 10);
}
</script>

<template>
  <section v-if="hasAny" class="film-release-dates stack-tight" data-testid="film-release-dates">
    <div v-for="kind in kinds" :key="kind" class="film-release-row">
      <span class="film-release-kind">{{ filmReleaseRowLabel(kind) }}</span>
      <div class="film-release-lines">
        <p v-for="region in regions" :key="`${kind}-${region}`" class="film-release-line">
          <span class="film-release-country">{{ regionLineLabel(region) }}</span>
          <template v-if="rawDate(region, kind)">
            <span class="film-release-date" :class="{ 'film-release-trigger-date': dateHighlighted(region, kind) }">
              {{ " " }}{{ formatFilmReleaseShort(rawDate(region, kind)) }}
            </span>
          </template>
          <span v-else class="film-release-missing">, pas encore annoncée</span>
        </p>
      </div>
    </div>
    <p v-if="footerParts" class="film-release-footer hint">
      {{ footerParts.prefix }}<span class="film-release-trigger-date">{{ footerParts.date }}</span>{{ footerParts.suffix }}
    </p>
  </section>
</template>

<style scoped>
.film-release-dates {
  margin: 0.35rem 0 0.65rem;
  gap: 0.55rem;
}

.film-release-row {
  display: grid;
  grid-template-columns: minmax(5.5rem, auto) 1fr;
  gap: 0.35rem 0.75rem;
  align-items: start;
}

.film-release-kind {
  font-weight: 600;
  color: var(--text);
}

.film-release-lines {
  display: flex;
  flex-direction: column;
  gap: 0.15rem;
}

.film-release-line {
  margin: 0;
  font-size: 0.92rem;
}

.film-release-missing {
  color: var(--muted);
}

.film-release-trigger-date {
  color: var(--warn);
  font-weight: 600;
}

.film-release-footer {
  margin: 0.15rem 0 0;
  color: var(--muted);
}

.film-release-footer .film-release-trigger-date {
  color: var(--warn);
  font-weight: 600;
}

@media (max-width: 420px) {
  .film-release-row {
    grid-template-columns: 1fr;
    gap: 0.2rem;
  }
}
</style>