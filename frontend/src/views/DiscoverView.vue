<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue";
import { useRouter } from "vue-router";

import MediaQuickView from "../components/MediaQuickView.vue";
import TmdbPosterImg from "../components/TmdbPosterImg.vue";
import { useDiscoverSwipe } from "../composables/useDiscoverSwipe";
import { api, userFacingApiError } from "../api";
import {
  markDiscoverPass,
  markGesturesHelpSeen,
  openDiscoverStores,
  readPassKeys,
  removeDiscoverCache,
  gesturesHelpSeen,
  upsertDiscoverCache,
} from "../discoverCache";
import { discoverInfoLine } from "../discoverFormat";
import {
  dropCard,
  flattenHomeRows,
  mergeReloadDeck,
  moveCardToEnd,
  type TmdbHomePayload,
} from "../discoverPack";
import type { DiscoverCard, DiscoverGesture, DiscoverMediaFilter } from "../discoverTypes";
import type { QuickTarget } from "../quick";

const router = useRouter();

const deck = ref<DiscoverCard[]>([]);
const laterSession = ref<Set<string>>(new Set());
const loading = ref(false);
const error = ref("");
const filterOpen = ref(false);
const sheetOpen = ref(false);
const helpOpen = ref(false);
const quick = ref<QuickTarget | null>(null);
const quickFollow = ref(false);
const mediaFilter = ref<DiscoverMediaFilter>("all");
const releaseKind = ref<"streaming" | "cinema" | "physical" | "new" | "on_air">("streaming");
const cardRoot = ref<HTMLElement | null>(null);
const overviewScroll = ref<HTMLElement | null>(null);
const followBusy = ref(false);
let sheetSwipeStartY = 0;
let sheetSwipeActive = false;

const current = computed(() => deck.value[0] ?? null);

const stampVisible = computed(() => swipe.dx.value > 48 && Math.abs(swipe.dx.value) > Math.abs(swipe.dy.value));

const cardStyle = computed(() => {
  const tilt = Math.max(-18, Math.min(18, swipe.dx.value / 12));
  return {
    transform: `translate(${swipe.dx.value}px, ${swipe.dy.value}px) rotate(${tilt}deg)`,
  };
});

function dismiss() {
  if (window.history.length > 1) {
    router.back();
    return;
  }
  void router.push("/");
}

function cardToQuick(card: DiscoverCard): QuickTarget {
  const kind = card.media_type === "film" ? "movie" : "tv";
  return {
    title: card.title,
    year: card.year ?? null,
    overview: card.overview ?? null,
    posterUrl: card.poster_url ?? null,
    mediaType: card.media_type,
    followed: Boolean(card.followed),
    tmdbId: card.tmdb_id,
    tmdbUrl: card.tmdb_url || `https://www.themoviedb.org/${kind}/${card.tmdb_id}`,
  };
}

async function followFilm(card: DiscoverCard) {
  if (followBusy.value) {
    return;
  }
  followBusy.value = true;
  error.value = "";
  try {
    await api("/api/v1/media", {
      method: "POST",
      body: JSON.stringify({
        start_now: true,
        type: "film",
        title: card.title,
        year: card.year ?? undefined,
        tmdb_id: card.tmdb_id,
        overview: card.overview ?? undefined,
        poster_url: card.poster_url ?? undefined,
      }),
    });
  } finally {
    followBusy.value = false;
  }
}

function evictFollowed(card: DiscoverCard) {
  removeDiscoverCache(card);
  deck.value = dropCard(deck.value, card);
}

function openSeriesFollow(card: DiscoverCard) {
  quickFollow.value = true;
  quick.value = cardToQuick(card);
}

function closeSheet() {
  sheetOpen.value = false;
}

function onSheetBackdropPointerDown(event: PointerEvent) {
  if (event.button !== 0 || event.target !== event.currentTarget) {
    return;
  }
  sheetSwipeActive = true;
  sheetSwipeStartY = event.clientY;
}

function onSheetDragStart(event: PointerEvent) {
  if (event.button !== 0) {
    return;
  }
  const target = event.target as HTMLElement | null;
  if (target?.closest(".discover-overview") && overviewScroll.value && overviewScroll.value.scrollTop > 0) {
    sheetSwipeActive = false;
    return;
  }
  sheetSwipeActive = true;
  sheetSwipeStartY = event.clientY;
}

function onSheetDragEnd(event: PointerEvent) {
  if (!sheetSwipeActive) {
    return;
  }
  sheetSwipeActive = false;
  if (event.clientY - sheetSwipeStartY > 72) {
    closeSheet();
  }
}

async function onGesture(gesture: DiscoverGesture) {
  if (sheetOpen.value || helpOpen.value || filterOpen.value || followBusy.value || quick.value) {
    return;
  }
  const card = current.value;
  if (!card) {
    return;
  }
  if (gesture === "sheet") {
    sheetOpen.value = true;
    return;
  }
  if (gesture === "follow") {
    if (card.media_type === "film") {
      try {
        await followFilm(card);
        evictFollowed(card);
      } catch (err) {
        error.value = userFacingApiError(err, "Suivi impossible.");
      }
      return;
    }
    openSeriesFollow(card);
    return;
  }
  if (gesture === "pass") {
    markDiscoverPass(card);
    deck.value = dropCard(deck.value, card);
    return;
  }
  if (gesture === "later") {
    const key = `${card.media_type}:${card.tmdb_id}`;
    laterSession.value.add(key);
    deck.value = moveCardToEnd(deck.value, card);
  }
}

const swipe = useDiscoverSwipe(onGesture);

watch(cardRoot, (el) => {
  if (el) {
    swipe.bind(el);
  }
});

async function loadDeck() {
  loading.value = true;
  error.value = "";
  const previous = deck.value;
  try {
    let rows: DiscoverCard[] = [];
    if (mediaFilter.value === "all" && releaseKind.value === "streaming") {
      const home = await api<TmdbHomePayload>("/api/v1/tmdb-trial/home");
      rows = flattenHomeRows(home);
    } else if (mediaFilter.value === "all") {
      const [films, series] = await Promise.all([
        api<{ items: DiscoverCard[] }>(
          `/api/v1/tmdb-trial/browse?type=film&release_kind=${releaseKind.value}&page=1&followed=false`,
        ),
        api<{ items: DiscoverCard[] }>(
          `/api/v1/tmdb-trial/browse?type=series&release_kind=${releaseKind.value === "streaming" ? "new" : releaseKind.value}&page=1&followed=false`,
        ),
      ]);
      rows = [...films.items, ...series.items];
    } else {
      const kind = mediaFilter.value;
      const rk =
        kind === "film"
          ? releaseKind.value
          : releaseKind.value === "streaming"
            ? "new"
            : releaseKind.value;
      const page = await api<{ items: DiscoverCard[] }>(
        `/api/v1/tmdb-trial/browse?type=${kind}&release_kind=${rk}&page=1&followed=false`,
      );
      rows = page.items;
    }
    for (const card of rows) {
      upsertDiscoverCache(card);
    }
    const pass = readPassKeys();
    deck.value = mergeReloadDeck(
      rows,
      { passKeys: pass, mediaFilter: mediaFilter.value, unfollowedOnly: true },
      laterSession.value,
      previous,
    );
  } catch (err) {
    error.value = userFacingApiError(err, "Chargement impossible.");
    deck.value = [];
  } finally {
    loading.value = false;
  }
}

function onQuickChanged() {
  const card = current.value;
  if (card) {
    evictFollowed(card);
  }
  quick.value = null;
  quickFollow.value = false;
}

async function sheetFollow() {
  const card = current.value;
  if (!card || followBusy.value) {
    return;
  }
  sheetOpen.value = false;
  if (card.media_type === "film") {
    try {
      await followFilm(card);
      evictFollowed(card);
    } catch (err) {
      error.value = userFacingApiError(err, "Suivi impossible.");
    }
    return;
  }
  openSeriesFollow(card);
}

function sheetLater() {
  sheetOpen.value = false;
  const card = current.value;
  if (!card) {
    return;
  }
  laterSession.value.add(`${card.media_type}:${card.tmdb_id}`);
  deck.value = moveCardToEnd(deck.value, card);
}

function closeHelp() {
  helpOpen.value = false;
  markGesturesHelpSeen();
}

onMounted(() => {
  openDiscoverStores();
  helpOpen.value = !gesturesHelpSeen();
  void loadDeck();
});
</script>

<template>
  <section class="discover-screen">
    <header class="discover-top">
      <button type="button" class="discover-icon" aria-label="Fermer" @click="dismiss">×</button>
      <button type="button" class="discover-icon" aria-label="Filtres" @click="filterOpen = !filterOpen">☰</button>
    </header>

    <div v-if="filterOpen" class="discover-filters">
      <p class="hint">Type</p>
      <div class="discover-chips">
        <button type="button" :class="{ active: mediaFilter === 'all' }" @click="mediaFilter = 'all'">Tous</button>
        <button type="button" :class="{ active: mediaFilter === 'film' }" @click="mediaFilter = 'film'">Films</button>
        <button type="button" :class="{ active: mediaFilter === 'series' }" @click="mediaFilter = 'series'">Séries</button>
      </div>
      <p class="hint">Sortie</p>
      <div class="discover-chips">
        <button type="button" :class="{ active: releaseKind === 'streaming' }" @click="releaseKind = 'streaming'">Streaming</button>
        <button type="button" :class="{ active: releaseKind === 'cinema' }" @click="releaseKind = 'cinema'">Cinéma</button>
        <button type="button" :class="{ active: releaseKind === 'physical' }" @click="releaseKind = 'physical'">Physique</button>
        <button type="button" :class="{ active: releaseKind === 'new' }" @click="releaseKind = 'new'">Nouvelles séries</button>
        <button type="button" :class="{ active: releaseKind === 'on_air' }" @click="releaseKind = 'on_air'">En cours</button>
      </div>
      <button type="button" class="discover-apply" @click="filterOpen = false; void loadDeck()">Appliquer</button>
    </div>

    <p v-if="error" class="discover-error" role="alert">{{ error }}</p>
    <p v-if="loading" class="discover-hint">Chargement…</p>
    <p v-else-if="!current" class="discover-hint">Plus de suggestions pour l’instant.</p>

    <article v-if="current" ref="cardRoot" class="discover-card" :style="cardStyle">
      <TmdbPosterImg class="discover-backdrop" :url="current.poster_url" :alt="current.title" :lazy="false" />
      <div v-if="stampVisible" class="discover-stamp">Suivre</div>
      <footer class="discover-footer">
        <h1>{{ current.title }}</h1>
        <p class="discover-meta">{{ discoverInfoLine(current) }}</p>
      </footer>
    </article>

    <div
      v-if="sheetOpen && current"
      class="discover-sheet"
      role="dialog"
      aria-modal="true"
      @click.self="closeSheet"
      @pointerdown="onSheetBackdropPointerDown"
      @pointerup="onSheetDragEnd"
      @pointercancel="onSheetDragEnd"
    >
      <div class="discover-sheet-inner" @click.stop>
        <header
          class="discover-sheet-head"
          @pointerdown="onSheetDragStart"
          @pointerup="onSheetDragEnd"
          @pointercancel="onSheetDragEnd"
        >
          <div class="discover-sheet-handle" aria-hidden="true" />
          <button type="button" class="discover-sheet-close" aria-label="Fermer" @click="closeSheet">×</button>
          <h2>{{ current.title }}</h2>
          <p class="discover-meta">{{ discoverInfoLine(current) }}</p>
        </header>
        <p
          v-if="current.overview"
          ref="overviewScroll"
          class="discover-overview"
          @pointerdown="onSheetDragStart"
          @pointerup="onSheetDragEnd"
          @pointercancel="onSheetDragEnd"
        >{{ current.overview }}</p>
        <a
          v-if="current.tmdb_url"
          class="discover-tmdb"
          :href="current.tmdb_url"
          target="_blank"
          rel="noopener noreferrer"
        >Fiche TMDB</a>
        <div class="discover-sheet-actions">
          <button type="button" class="ghost" @click="sheetLater">Plus tard</button>
          <button type="button" :disabled="followBusy" @click="sheetFollow">Suivre</button>
        </div>
      </div>
    </div>

    <div v-if="helpOpen" class="discover-help" role="dialog" aria-modal="true">
      <div class="discover-help-card">
        <h2>Les gestes</h2>
        <p>Glissez la carte pour choisir.</p>
        <ul>
          <li><strong>→</strong> Suivre</li>
          <li><strong>←</strong> Passer</li>
          <li><strong>↑</strong> Voir la fiche</li>
          <li><strong>↓</strong> Plus tard</li>
        </ul>
        <button type="button" class="discover-apply" @click="closeHelp">Compris</button>
      </div>
    </div>

    <MediaQuickView
      :target="quick"
      :follow="quickFollow"
      :stack-on-top="true"
      @close="quick = null"
      @changed="onQuickChanged"
    />
  </section>
</template>

<style scoped>
.discover-screen {
  position: fixed;
  inset: 0;
  z-index: 40;
  background: #0a0a0c;
  color: #fff;
  overflow: hidden;
}

.discover-top {
  position: absolute;
  top: 0;
  left: 0;
  right: 0;
  z-index: 5;
  display: flex;
  justify-content: space-between;
  padding: 0.75rem 1rem;
}

.discover-icon {
  width: 2.5rem;
  height: 2.5rem;
  border: 0;
  border-radius: 999px;
  background: rgb(0 0 0 / 45%);
  color: #fff;
  font-size: 1.4rem;
  line-height: 1;
}

.discover-card {
  position: absolute;
  inset: 0;
  touch-action: none;
  user-select: none;
}

.discover-backdrop :deep(img) {
  width: 100%;
  height: 100%;
  object-fit: cover;
}

.discover-backdrop {
  display: block;
  width: 100%;
  height: 100%;
}

.discover-footer {
  position: absolute;
  left: 0;
  right: 0;
  bottom: 0;
  padding: 4rem 1.25rem 2rem;
  background: linear-gradient(180deg, transparent, rgb(0 0 0 / 85%));
}

.discover-footer h1 {
  margin: 0 0 0.35rem;
  font-size: 1.75rem;
}

.discover-meta {
  margin: 0;
  opacity: 0.85;
}

.discover-stamp {
  position: absolute;
  top: 35%;
  left: 18%;
  padding: 0.4rem 1.2rem;
  border: 3px solid #6ee7a2;
  border-radius: 0.5rem;
  color: #6ee7a2;
  font-weight: 700;
  letter-spacing: 0.12em;
  transform: rotate(-18deg);
  background: rgb(0 0 0 / 35%);
}

.discover-sheet,
.discover-help {
  position: absolute;
  inset: 0;
  z-index: 6;
  display: grid;
  place-items: end center;
  background: rgb(0 0 0 / 50%);
  padding: 1rem;
}

.discover-sheet-inner,
.discover-help-card {
  width: min(100%, 28rem);
  border-radius: 1rem 1rem 0.25rem 0.25rem;
  background: #17181c;
  padding: 1.25rem;
  color: #fff;
}

.discover-overview {
  margin: 0.75rem 0;
  line-height: 1.45;
  max-height: 9rem;
  overflow: auto;
}

.discover-tmdb {
  color: #9fd3ff;
}

.discover-sheet-actions {
  display: flex;
  gap: 0.75rem;
  margin-top: 1rem;
}

.discover-sheet-actions .ghost {
  flex: 1;
  border: 1px solid rgb(255 255 255 / 35%);
  background: transparent;
  color: #fff;
  border-radius: 0.65rem;
  padding: 0.65rem;
}

.discover-sheet-actions button:last-child {
  flex: 1;
  border: 0;
  border-radius: 0.65rem;
  padding: 0.65rem;
  background: #3b82f6;
  color: #fff;
}

.discover-filters {
  position: absolute;
  top: 3.5rem;
  right: 0.75rem;
  z-index: 5;
  width: min(16rem, 92vw);
  padding: 0.75rem;
  border-radius: 0.75rem;
  background: rgb(8 8 12 / 92%);
}

.discover-chips {
  display: flex;
  flex-wrap: wrap;
  gap: 0.35rem;
  margin-bottom: 0.65rem;
}

.discover-chips button {
  border: 1px solid rgb(255 255 255 / 25%);
  background: transparent;
  color: #fff;
  border-radius: 999px;
  padding: 0.25rem 0.65rem;
  font-size: 0.85rem;
}

.discover-chips button.active {
  background: #fff;
  color: #111;
}

.discover-apply {
  width: 100%;
  margin-top: 0.5rem;
  border: 0;
  border-radius: 0.65rem;
  padding: 0.6rem;
  background: #3b82f6;
  color: #fff;
}

.discover-hint {
  position: absolute;
  inset: 0;
  display: grid;
  place-items: center;
  padding: 1rem;
  text-align: center;
}

.discover-error {
  position: absolute;
  top: 4.25rem;
  left: 0;
  right: 0;
  z-index: 9;
  display: block;
  margin: 0 1rem;
  padding: 0.6rem 0.85rem;
  border-radius: 0.65rem;
  background: rgb(40 12 12 / 92%);
  color: #ffb4b4;
  text-align: left;
}

.discover-sheet-inner {
  position: relative;
}

.discover-sheet-head {
  padding-top: 0.35rem;
}

.discover-sheet-handle {
  width: 2.5rem;
  height: 0.25rem;
  margin: 0 auto 0.75rem;
  border-radius: 999px;
  background: rgb(255 255 255 / 35%);
}

.discover-sheet-close {
  position: absolute;
  top: 0.65rem;
  right: 0.65rem;
  width: 2rem;
  height: 2rem;
  border: 0;
  border-radius: 999px;
  background: rgb(255 255 255 / 12%);
  color: #fff;
  font-size: 1.2rem;
  line-height: 1;
}

.discover-sheet-actions button:disabled {
  opacity: 0.55;
}

.discover-help ul {
  padding-left: 0;
  list-style: none;
}

.discover-help li {
  margin: 0.35rem 0;
}
</style>