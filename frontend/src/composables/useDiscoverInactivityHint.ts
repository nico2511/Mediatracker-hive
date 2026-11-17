import { onMounted, onUnmounted, watch, type ComputedRef, type Ref } from "vue";

import { prefersReducedMotion } from "../discoverMotion";

export const DISCOVER_INACTIVITY_HINT_MS = 6000;
export const DISCOVER_INACTIVITY_MAX_HINTS = 3;

export interface DiscoverInactivityHintContext {
  helpOpen: Ref<boolean>;
  filterOpen: Ref<boolean>;
  sheetPresent: Ref<boolean>;
  quickOpen: ComputedRef<boolean> | Ref<boolean>;
  followBusy: Ref<boolean>;
  loading: Ref<boolean>;
  currentCardKey: ComputedRef<string | null> | Ref<string | null>;
  dragging: Ref<boolean>;
  exiting: Ref<boolean>;
  parkedAfterExit: Ref<boolean>;
  motionActive: Ref<boolean>;
  playIntroDemo: () => Promise<void>;
}

export interface DiscoverInactivityHintControls {
  bump: () => void;
  onGesture: () => void;
}

export function useDiscoverInactivityHint(ctx: DiscoverInactivityHintContext): DiscoverInactivityHintControls {
  let timer: ReturnType<typeof setTimeout> | null = null;
  let hintsForKey: string | null = null;
  let hintCount = 0;
  let introPlaying = false;

  const clearTimer = () => {
    if (timer !== null) {
      clearTimeout(timer);
      timer = null;
    }
  };

  const blocked = (): boolean => {
    if (prefersReducedMotion()) {
      return true;
    }
    if (typeof document !== "undefined" && document.visibilityState !== "visible") {
      return true;
    }
    if (ctx.helpOpen.value || ctx.filterOpen.value || ctx.sheetPresent.value) {
      return true;
    }
    if (ctx.quickOpen.value || ctx.followBusy.value || ctx.loading.value) {
      return true;
    }
    if (!ctx.currentCardKey.value) {
      return true;
    }
    if (ctx.dragging.value || ctx.exiting.value || ctx.parkedAfterExit.value) {
      return true;
    }
    if (ctx.motionActive.value || introPlaying) {
      return true;
    }
    return false;
  };

  const atHintLimit = (): boolean => {
    const key = ctx.currentCardKey.value;
    if (!key) {
      return true;
    }
    return hintsForKey === key && hintCount >= DISCOVER_INACTIVITY_MAX_HINTS;
  };

  const syncCardKey = (key: string | null) => {
    if (key !== hintsForKey) {
      hintsForKey = key;
      hintCount = 0;
    }
  };

  const schedule = () => {
    clearTimer();
    if (blocked() || atHintLimit()) {
      return;
    }
    timer = setTimeout(() => {
      timer = null;
      void fireHint();
    }, DISCOVER_INACTIVITY_HINT_MS);
  };

  const fireHint = async () => {
    if (blocked() || atHintLimit()) {
      schedule();
      return;
    }
    const key = ctx.currentCardKey.value;
    if (!key) {
      return;
    }
    syncCardKey(key);
    hintCount += 1;
    introPlaying = true;
    try {
      await ctx.playIntroDemo();
    } finally {
      introPlaying = false;
      if (!atHintLimit()) {
        schedule();
      }
    }
  };

  const bump = () => {
    syncCardKey(ctx.currentCardKey.value);
    schedule();
  };

  const onGesture = () => {
    bump();
  };

  const onVisibilityChange = () => {
    bump();
  };

  watch(ctx.currentCardKey, (key) => {
    syncCardKey(key);
    bump();
  });

  watch(
    [
      ctx.helpOpen,
      ctx.filterOpen,
      ctx.sheetPresent,
      ctx.quickOpen,
      ctx.followBusy,
      ctx.loading,
      ctx.dragging,
      ctx.exiting,
      ctx.parkedAfterExit,
      ctx.motionActive,
    ],
    () => {
      if (blocked() || atHintLimit()) {
        clearTimer();
        return;
      }
      if (!timer) {
        schedule();
      }
    },
  );

  onMounted(() => {
    if (typeof document !== "undefined") {
      document.addEventListener("visibilitychange", onVisibilityChange);
    }
    bump();
  });

  onUnmounted(() => {
    clearTimer();
    if (typeof document !== "undefined") {
      document.removeEventListener("visibilitychange", onVisibilityChange);
    }
  });

  return { bump, onGesture };
}