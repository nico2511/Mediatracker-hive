<script setup lang="ts">
import { computed, nextTick, onMounted, ref, watch } from "vue";
import { useRouter } from "vue-router";

import MediaQuickView from "../components/MediaQuickView.vue";
import TmdbPosterImg from "../components/TmdbPosterImg.vue";
import { useDiscoverInactivityHint } from "../composables/useDiscoverInactivityHint";
import { useDiscoverSwipe } from "../composables/useDiscoverSwipe";
import {
  animateDiscoverScalars,
  type DiscoverRafAnimator,
  prefersReducedMotion,
  underCardTransform,
} from "../discoverMotion";
import {
  discoverEdgeReleaseHint,
  discoverFollowStampVisible,
  type DiscoverEdgeSide,
} from "../discoverGestures";
import { api, userFacingApiError } from "../api";
import { tmdbPage } from "../labels";
import {
  markDiscoverPass,
  markGesturesHelpSeen,
  openDiscoverStores,
  readPassKeys,
  removeDiscoverCache,
  discoverEntryKey,
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
const sheetPresent = ref(false);
const sheetBackdropOpacity = ref(0);
const sheetInnerOffset = ref(0);
const sheetPointerDragging = ref(false);
let sheetMotion: DiscoverRafAnimator | null = null;
const helpOpen = ref(false);
const quick = ref<QuickTarget | null>(null);
const quickFollow = ref(false);
const mediaFilter = ref<DiscoverMediaFilter>("all");
const releaseKind = ref<"streaming" | "cinema" | "physical" | "new" | "on_air">("streaming");
const cardRoot = ref<HTMLElement | null>(null);
const overviewScroll = ref<HTMLElement | null>(null);
const followBusy = ref(false);
let sheetSwipeStartY = 0;
let sheetSwipeStartOffset = 0;

const current = computed(() => deck.value[0] ?? null);
const nextCard = computed(() => deck.value[1] ?? null);
const currentCardKey = computed(() =>
  current.value ? discoverEntryKey(current.value.media_type, current.value.tmdb_id) : null,
);
const quickOpen = computed(() => quick.value !== null);

const stampVisible = computed(() =>
  discoverFollowStampVisible(swipe.dx.value, swipe.dy.value, swipe.gestureThresholds.value),
);

const touchEdgeHint = computed(() => {
  if (!swipe.touchDrag.value || !swipe.dragging.value) {
    return null;
  }
  return discoverEdgeReleaseHint(swipe.dx.value, swipe.dy.value, swipe.gestureThresholds.value);
});

const touchEdgeIntensity = computed(() => {
  const hint = touchEdgeHint.value;
  if (!hint) {
    return 0;
  }
  return hint.atThreshold ? 1 : swipe.dragProgress.value;
});

const touchEdgeSides: DiscoverEdgeSide[] = ["left", "right", "top", "bottom"];

const touchEdgeBarStyle = (side: DiscoverEdgeSide): Record<string, string> => {
  const hint = touchEdgeHint.value;
  const active = hint?.side === side;
  const intensity = active ? touchEdgeIntensity.value : 0;
  const edgeColors: Record<DiscoverEdgeSide, string> = {
    right: "110, 231, 162",
    left: "248, 113, 113",
    top: "147, 197, 253",
    bottom: "251, 191, 36",
  };
  const alpha = 0.12 + intensity * 0.68;
  const rgb = edgeColors[side];
  const base: Record<string, string> = {
    opacity: intensity > 0 ? "1" : "0",
    pointerEvents: "none",
  };
  if (prefersReducedMotion()) {
    base.transition = "none";
  }
  if (side === "left") {
    base.background = `linear-gradient(90deg, rgb(${rgb} / ${alpha.toFixed(3)}), transparent 70%)`;
  } else if (side === "right") {
    base.background = `linear-gradient(270deg, rgb(${rgb} / ${alpha.toFixed(3)}), transparent 70%)`;
  } else if (side === "top") {
    base.background = `linear-gradient(180deg, rgb(${rgb} / ${alpha.toFixed(3)}), transparent 70%)`;
  } else {
    base.background = `linear-gradient(0deg, rgb(${rgb} / ${alpha.toFixed(3)}), transparent 70%)`;
  }
  return base;
};

const activeCardStyle = computed(() => ({
  transform: `translate3d(${swipe.dx.value}px, ${swipe.dy.value}px, 0) rotate(${swipe.rotate.value}deg)`,
  opacity: String(swipe.opacity.value),
}));

const underCardStyle = computed(() => {
  const { scale, translateY } = underCardTransform(swipe.exiting.value ? 1 : swipe.dragProgress.value);
  return {
    transform: `translate3d(0, ${translateY}px, 0) scale(${scale})`,
    opacity: String(0.9 + (swipe.exiting.value ? 0.1 : swipe.dragProgress.value * 0.1)),
  };
});

const sheetRootStyle = computed(() => ({
  background: `rgb(0 0 0 / ${(sheetBackdropOpacity.value * 0.52).toFixed(3)})`,
}));

const sheetInnerStyle = computed(() => ({
  transform: `translate3d(0, ${sheetInnerOffset.value}px, 0)`,
}));

function stopSheetMotion() {
  sheetMotion?.cancel();
  sheetMotion = null;
}

function runSheetMotion(targetOffset: number, targetBackdrop: number, onDone?: () => void) {
  stopSheetMotion();
  const from = { offset: sheetInnerOffset.value, backdrop: sheetBackdropOpacity.value };
  const to = { offset: targetOffset, backdrop: targetBackdrop };
  sheetMotion = animateDiscoverScalars(
    from,
    to,
    "sheet",
    (frame) => {
      sheetInnerOffset.value = frame.offset ?? 0;
      sheetBackdropOpacity.value = frame.backdrop ?? 0;
    },
    () => {
      sheetMotion = null;
      onDone?.();
    },
  );
}

function revealSheet() {
  sheetOpen.value = true;
  sheetPresent.value = true;
  const startOffset = prefersReducedMotion() ? 0 : Math.min(420, typeof window !== "undefined" ? window.innerHeight * 0.45 : 320);
  sheetInnerOffset.value = startOffset;
  sheetBackdropOpacity.value = prefersReducedMotion() ? 1 : 0;
  void nextTick(() => {
    runSheetMotion(0, 1);
  });
}

function closeSheet() {
  if (!sheetPresent.value) {
    return;
  }
  const offscreen = typeof window !== "undefined" ? Math.max(280, window.innerHeight * 0.55) : 360;
  runSheetMotion(offscreen, 0, () => {
    sheetOpen.value = false;
    sheetPresent.value = false;
    sheetInnerOffset.value = 0;
    sheetBackdropOpacity.value = 0;
    inactivityBump();
  });
}

function dismiss() {
  if (window.history.length > 1) {
    router.back();
    return;
  }
  void router.push("/");
}

function cardToQuick(card: DiscoverCard): QuickTarget {
  return {
    title: card.title,
    year: card.year ?? null,
    overview: card.overview ?? null,
    posterUrl: card.poster_url ?? null,
    mediaType: card.media_type,
    followed: Boolean(card.followed),
    tmdbId: card.tmdb_id,
    tmdbUrl: card.tmdb_url || tmdbPage(card.tmdb_id, card.media_type),
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

function onSheetBackdropPointerDown(event: PointerEvent) {
  if (event.button !== 0 || event.target !== event.currentTarget) {
    return;
  }
  sheetPointerDragging.value = true;
  sheetSwipeStartY = event.clientY;
  sheetSwipeStartOffset = sheetInnerOffset.value;
  stopSheetMotion();
}

function onSheetDragStart(event: PointerEvent) {
  if (event.button !== 0) {
    return;
  }
  const target = event.target as HTMLElement | null;
  if (target?.closest(".discover-overview") && overviewScroll.value && overviewScroll.value.scrollTop > 0) {
    sheetPointerDragging.value = false;
    return;
  }
  sheetPointerDragging.value = true;
  sheetSwipeStartY = event.clientY;
  sheetSwipeStartOffset = sheetInnerOffset.value;
  stopSheetMotion();
}

function onSheetDragMove(event: PointerEvent) {
  if (!sheetPointerDragging.value) {
    return;
  }
  const delta = event.clientY - sheetSwipeStartY;
  const offset = Math.max(0, sheetSwipeStartOffset + delta);
  sheetInnerOffset.value = offset;
  sheetBackdropOpacity.value = Math.max(0, Math.min(1, 1 - offset / 280));
}

function onSheetDragEnd(event: PointerEvent) {
  if (!sheetPointerDragging.value) {
    return;
  }
  sheetPointerDragging.value = false;
  if (event.clientY - sheetSwipeStartY > 72 || sheetInnerOffset.value > 96) {
    closeSheet();
    return;
  }
  runSheetMotion(0, 1);
}

function recoverParkedIfCurrentUnchanged(keyBefore: string | null) {
  if (!keyBefore || !swipe.parkedAfterExit.value) {
    return;
  }
  const now = current.value;
  const keyAfter = now ? discoverEntryKey(now.media_type, now.tmdb_id) : null;
  if (keyAfter === keyBefore) {
    swipe.recoverFromParked();
  }
}

function recoverParkedIfGestureAborted() {
  if (swipe.parkedAfterExit.value) {
    swipe.recoverFromParked();
  }
}

async function onGesture(gesture: DiscoverGesture) {
  inactivity.onGesture();
  if (sheetOpen.value || helpOpen.value || filterOpen.value || followBusy.value || quick.value) {
    recoverParkedIfGestureAborted();
    return;
  }
  const card = current.value;
  if (!card) {
    recoverParkedIfGestureAborted();
    return;
  }
  const keyBefore = discoverEntryKey(card.media_type, card.tmdb_id);
  if (gesture === "sheet") {
    revealSheet();
    return;
  }
  if (gesture === "follow") {
    if (card.media_type === "film") {
      try {
        await followFilm(card);
        evictFollowed(card);
      } catch (err) {
        error.value = userFacingApiError(err, "Suivi impossible.");
        swipe.recoverFromParked();
      }
      return;
    }
    openSeriesFollow(card);
    return;
  }
  if (gesture === "pass") {
    markDiscoverPass(card);
    deck.value = dropCard(deck.value, card);
    recoverParkedIfCurrentUnchanged(keyBefore);
    return;
  }
  if (gesture === "later") {
    laterSession.value.add(keyBefore);
    deck.value = moveCardToEnd(deck.value, card);
    recoverParkedIfCurrentUnchanged(keyBefore);
  }
}

const swipe = useDiscoverSwipe(onGesture);

const inactivity = useDiscoverInactivityHint({
  helpOpen,
  filterOpen,
  sheetPresent,
  quickOpen,
  followBusy,
  loading,
  currentCardKey,
  dragging: swipe.dragging,
  exiting: swipe.exiting,
  parkedAfterExit: swipe.parkedAfterExit,
  motionActive: swipe.motionActive,
  playIntroDemo: () => swipe.playIntroDemo(),
});

function inactivityBump() {
  inactivity.bump();
}

watch(
  () => (current.value ? discoverEntryKey(current.value.media_type, current.value.tmdb_id) : null),
  () => {
    if (swipe.parkedAfterExit.value) {
      swipe.clearParkedSilent();
    }
  },
);

watch(cardRoot, (el) => {
  if (el) {
    swipe.bind(el);
  }
});

function onQuickDismiss() {
  quick.value = null;
  quickFollow.value = false;
  if (swipe.parkedAfterExit.value) {
    swipe.recoverFromParked();
  }
  inactivityBump();
}

function relancerSuggestions() {
  laterSession.value = new Set();
  void loadDeck();
}

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
  closeSheet();
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
  closeSheet();
  const card = current.value;
  if (!card) {
    return;
  }
  laterSession.value.add(`${card.media_type}:${card.tmdb_id}`);
  deck.value = moveCardToEnd(deck.value, card);
}

function openHelp() {
  helpOpen.value = true;
}

function closeHelp() {
  const firstEver = !gesturesHelpSeen();
  helpOpen.value = false;
  markGesturesHelpSeen();
  inactivityBump();
  if (firstEver && current.value) {
    void swipe.playIntroDemo();
  }
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
      <div class="discover-top-end">
        <button type="button" class="discover-icon" aria-label="Aide aux gestes" @click="openHelp">?</button>
        <button type="button" class="discover-icon" aria-label="Filtres" @click="filterOpen = !filterOpen">☰</button>
      </div>
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
      <button type="button" class="discover-apply" @click="filterOpen = false; inactivityBump(); void loadDeck()">Appliquer</button>
    </div>

    <p
      v-if="touchEdgeHint"
      class="discover-touch-edge-caption"
      :class="{ 'discover-touch-edge-caption--ready': touchEdgeHint.atThreshold }"
      aria-live="polite"
    >
      <strong>{{ touchEdgeHint.actionLabel }}</strong>
      <span>{{ touchEdgeHint.hint }}</span>
    </p>

    <div
      v-for="side in touchEdgeSides"
      :key="side"
      class="discover-touch-edge"
      :class="`discover-touch-edge--${side}`"
      :style="touchEdgeBarStyle(side)"
      aria-hidden="true"
    />

    <p v-if="error" class="discover-error" role="alert">{{ error }}</p>
    <p v-if="loading" class="discover-hint">Chargement…</p>
    <template v-else-if="!current">
      <p class="discover-hint">Plus de suggestions pour l’instant.</p>
      <button type="button" class="discover-apply" @click="relancerSuggestions">Relancer</button>
    </template>

    <article
      v-if="nextCard && current"
      :key="discoverEntryKey(nextCard.media_type, nextCard.tmdb_id)"
      class="discover-card discover-card--under"
      :style="underCardStyle"
      aria-hidden="true"
    >
      <TmdbPosterImg class="discover-backdrop" :url="nextCard.poster_url" :alt="nextCard.title" :lazy="false" />
      <footer class="discover-footer">
        <h1>{{ nextCard.title }}</h1>
        <p class="discover-meta">{{ discoverInfoLine(nextCard) }}</p>
      </footer>
    </article>

    <article
      v-if="current"
      :key="discoverEntryKey(current.media_type, current.tmdb_id)"
      ref="cardRoot"
      class="discover-card discover-card--top"
      :class="{ 'discover-card--will-change': swipe.dragging.value || swipe.motionActive.value }"
      :style="activeCardStyle"
      @pointerdown.capture="inactivityBump"
    >
      <TmdbPosterImg class="discover-backdrop" :url="current.poster_url" :alt="current.title" :lazy="false" />
      <div v-if="stampVisible" class="discover-stamp">Suivre</div>
      <footer class="discover-footer">
        <h1>{{ current.title }}</h1>
        <p class="discover-meta">{{ discoverInfoLine(current) }}</p>
      </footer>
    </article>

    <div
      v-if="sheetPresent && current"
      class="discover-sheet"
      role="dialog"
      aria-modal="true"
      :style="sheetRootStyle"
      @click.self="closeSheet"
      @pointerdown="onSheetBackdropPointerDown"
      @pointermove="onSheetDragMove"
      @pointerup="onSheetDragEnd"
      @pointercancel="onSheetDragEnd"
    >
      <div class="discover-sheet-inner" :style="sheetInnerStyle" @click.stop>
        <header
          class="discover-sheet-head"
          @pointerdown="onSheetDragStart"
          @pointermove="onSheetDragMove"
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
          @pointermove="onSheetDragMove"
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

    <div v-if="helpOpen" class="discover-help" role="dialog" aria-modal="true" aria-labelledby="discover-help-title">
      <div class="discover-help-card">
        <h2 id="discover-help-title">Les gestes</h2>
        <p class="discover-help-lead">Glissez la carte pour choisir.</p>
        <ul class="discover-help-gestures">
          <li>
            <span class="discover-help-arrow discover-help-arrow--follow" aria-hidden="true">→</span>
            <span><strong>Suivre</strong></span>
          </li>
          <li>
            <span class="discover-help-arrow discover-help-arrow--pass" aria-hidden="true">←</span>
            <span><strong>Passer</strong></span>
          </li>
          <li>
            <span class="discover-help-arrow discover-help-arrow--sheet" aria-hidden="true">↑</span>
            <span><strong>Voir la fiche</strong> (synopsis, lien TMDB)</span>
          </li>
          <li>
            <span class="discover-help-arrow discover-help-arrow--later" aria-hidden="true">↓</span>
            <span><strong>Plus tard</strong> (revient en fin de pile)</span>
          </li>
        </ul>
        <p class="discover-help-sheet-hint">Touchez × ou glissez vers le bas pour fermer la fiche.</p>
        <button type="button" class="discover-apply" @click="closeHelp">Compris</button>
      </div>
    </div>

    <MediaQuickView
      :target="quick"
      :follow="quickFollow"
      :stack-on-top="true"
      @close="onQuickDismiss"
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

.discover-top-end {
  display: flex;
  gap: 0.5rem;
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
  transform-origin: center bottom;
}

.discover-card--under {
  z-index: 1;
  pointer-events: none;
}

.discover-card--top {
  z-index: 2;
}

.discover-card--will-change {
  will-change: transform, opacity;
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

.discover-touch-edge {
  position: absolute;
  z-index: 3;
  pointer-events: none;
  transition: opacity 120ms ease;
}

.discover-touch-edge--left,
.discover-touch-edge--right {
  top: 0;
  bottom: 0;
  width: min(42vw, 9rem);
}

.discover-touch-edge--left {
  left: 0;
}

.discover-touch-edge--right {
  right: 0;
}

.discover-touch-edge--top,
.discover-touch-edge--bottom {
  left: 0;
  right: 0;
  height: min(36vh, 8rem);
}

.discover-touch-edge--top {
  top: 0;
}

.discover-touch-edge--bottom {
  bottom: 0;
}

.discover-touch-edge-caption {
  position: absolute;
  left: 50%;
  bottom: 6.5rem;
  z-index: 4;
  transform: translateX(-50%);
  display: grid;
  gap: 0.15rem;
  text-align: center;
  padding: 0.45rem 0.85rem;
  border-radius: 0.65rem;
  background: rgb(0 0 0 / 45%);
  opacity: 0.85;
  transition: opacity 120ms ease;
  pointer-events: none;
}

.discover-touch-edge-caption strong {
  font-size: 1.05rem;
}

.discover-touch-edge-caption span {
  font-size: 0.85rem;
  opacity: 0.92;
}

.discover-touch-edge-caption--ready {
  opacity: 1;
  background: rgb(0 0 0 / 62%);
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
  padding: 1rem;
}

.discover-help {
  background: rgb(0 0 0 / 50%);
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
  transform-origin: center bottom;
  will-change: transform;
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

.discover-help-lead {
  margin: 0 0 0.75rem;
  opacity: 0.9;
}

.discover-help-gestures {
  padding-left: 0;
  list-style: none;
  margin: 0 0 0.75rem;
}

.discover-help-gestures li {
  display: flex;
  align-items: flex-start;
  gap: 0.65rem;
  margin: 0.5rem 0;
  line-height: 1.4;
}

.discover-help-arrow {
  flex-shrink: 0;
  display: inline-grid;
  place-items: center;
  width: 1.75rem;
  height: 1.75rem;
  border-radius: 0.4rem;
  background: rgb(255 255 255 / 10%);
  font-weight: 700;
  font-size: 1.1rem;
}

.discover-help-arrow--follow {
  animation: discover-hint-follow 1.8s ease-in-out infinite;
}

.discover-help-arrow--pass {
  animation: discover-hint-pass 1.8s ease-in-out infinite;
}

.discover-help-arrow--sheet {
  animation: discover-hint-sheet 1.8s ease-in-out infinite;
}

.discover-help-arrow--later {
  animation: discover-hint-later 1.8s ease-in-out infinite;
}

@keyframes discover-hint-follow {
  0%,
  100% {
    transform: translate3d(0, 0, 0);
  }

  50% {
    transform: translate3d(7px, 0, 0);
  }
}

@keyframes discover-hint-pass {
  0%,
  100% {
    transform: translate3d(0, 0, 0);
  }

  50% {
    transform: translate3d(-7px, 0, 0);
  }
}

@keyframes discover-hint-sheet {
  0%,
  100% {
    transform: translate3d(0, 0, 0);
  }

  50% {
    transform: translate3d(0, -7px, 0);
  }
}

@keyframes discover-hint-later {
  0%,
  100% {
    transform: translate3d(0, 0, 0);
  }

  50% {
    transform: translate3d(0, 7px, 0);
  }
}

.discover-help-sheet-hint {
  margin: 0 0 1rem;
  font-size: 0.9rem;
  opacity: 0.82;
  line-height: 1.4;
}

@media (prefers-reduced-motion: reduce) {
  .discover-touch-edge,
  .discover-touch-edge-caption {
    transition: none !important;
  }

  .discover-help-arrow {
    animation: none !important;
  }
  .discover-card--will-change {
    will-change: auto;
  }

  .discover-sheet-inner {
    will-change: auto;
  }
}
</style>