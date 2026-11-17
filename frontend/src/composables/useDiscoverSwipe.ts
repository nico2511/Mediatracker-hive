import { onUnmounted, ref, type Ref } from "vue";

import {
  animateDiscoverScalars,
  discoverCardTilt,
  discoverDragProgress,
  discoverExitTarget,
  prefersReducedMotion,
  type DiscoverRafAnimator,
} from "../discoverMotion";
import {
  discoverGestureThresholds,
  resolveDiscoverGesture,
  type DiscoverGestureThresholds,
} from "../discoverGestures";
import type { DiscoverGesture } from "../discoverTypes";

export interface DiscoverSwipeState {
  dx: Ref<number>;
  dy: Ref<number>;
  rotate: Ref<number>;
  opacity: Ref<number>;
  dragging: Ref<boolean>;
  exiting: Ref<boolean>;
  parkedAfterExit: Ref<boolean>;
  motionActive: Ref<boolean>;
  dragProgress: Ref<number>;
  touchDrag: Ref<boolean>;
  gestureThresholds: Ref<DiscoverGestureThresholds>;
  clearParkedSilent: () => void;
  recoverFromParked: () => void;
  playIntroDemo: () => Promise<void>;
  bind: (root: HTMLElement) => void;
  unbind: () => void;
}

export function useDiscoverSwipe(onGesture: (gesture: DiscoverGesture) => void): DiscoverSwipeState {
  const dx = ref(0);
  const dy = ref(0);
  const rotate = ref(0);
  const opacity = ref(1);
  const dragging = ref(false);
  const exiting = ref(false);
  const parkedAfterExit = ref(false);
  const motionActive = ref(false);
  const dragProgress = ref(0);
  const touchDrag = ref(false);
  const gestureThresholds = ref<DiscoverGestureThresholds>(discoverGestureThresholds(400, 700, false));

  let startX = 0;
  let startY = 0;
  let active = false;
  let rootEl: HTMLElement | null = null;
  let pendingMove: PointerEvent | null = null;
  let moveRaf = 0;
  let animator: DiscoverRafAnimator | null = null;
  let lastMoveX = 0;
  let lastMoveY = 0;
  let lastMoveT = 0;
  let velocityX = 0;
  let velocityY = 0;
  let introDemoActive = false;
  let introDemoFinish: (() => void) | null = null;
  let activeThresholds: DiscoverGestureThresholds = discoverGestureThresholds(400, 700, false);

  const refreshThresholds = (touch: boolean) => {
    const w = rootEl?.clientWidth ?? 400;
    const h = rootEl?.clientHeight ?? 700;
    activeThresholds = discoverGestureThresholds(w, h, touch);
    gestureThresholds.value = activeThresholds;
  };

  const syncDragProgress = () => {
    dragProgress.value = discoverDragProgress(dx.value, dy.value, activeThresholds);
  };

  const finishIntroDemo = () => {
    if (!introDemoActive && !introDemoFinish) {
      return;
    }
    introDemoActive = false;
    motionActive.value = false;
    dragProgress.value = 0;
    const finish = introDemoFinish;
    introDemoFinish = null;
    finish?.();
  };

  const stopAnimator = () => {
    animator?.cancel();
    animator = null;
    if (introDemoActive) {
      finishIntroDemo();
    }
  };

  const applyMove = (clientX: number, clientY: number) => {
    dx.value = clientX - startX;
    dy.value = clientY - startY;
    rotate.value = discoverCardTilt(dx.value);
    syncDragProgress();
    const now = performance.now();
    const dt = now - lastMoveT;
    if (dt > 0) {
      velocityX = (clientX - lastMoveX) / dt;
      velocityY = (clientY - lastMoveY) / dt;
    }
    lastMoveX = clientX;
    lastMoveY = clientY;
    lastMoveT = now;
  };

  const flushMove = () => {
    moveRaf = 0;
    if (!pendingMove) {
      return;
    }
    applyMove(pendingMove.clientX, pendingMove.clientY);
    pendingMove = null;
  };

  const resetVisual = () => {
    dx.value = 0;
    dy.value = 0;
    rotate.value = 0;
    opacity.value = 1;
    dragProgress.value = 0;
    exiting.value = false;
    parkedAfterExit.value = false;
    motionActive.value = false;
    touchDrag.value = false;
  };

  const clearParkedSilent = () => {
    stopAnimator();
    resetVisual();
  };

  const recoverFromParked = () => {
    if (!parkedAfterExit.value) {
      return;
    }
    springBack();
    parkedAfterExit.value = false;
  };

  const springBack = () => {
    stopAnimator();
    const from = { dx: dx.value, dy: dy.value, rotate: rotate.value, opacity: opacity.value };
    const to = { dx: 0, dy: 0, rotate: 0, opacity: 1 };
    motionActive.value = true;
    exiting.value = false;
    animator = animateDiscoverScalars(
      from,
      to,
      "spring",
      (frame) => {
        dx.value = frame.dx ?? 0;
        dy.value = frame.dy ?? 0;
        rotate.value = frame.rotate ?? 0;
        opacity.value = frame.opacity ?? 1;
        syncDragProgress();
      },
      () => {
        resetVisual();
        animator = null;
      },
    );
  };

  const settleAfterExit = () => {
    exiting.value = false;
    motionActive.value = false;
    parkedAfterExit.value = true;
    dragProgress.value = 1;
    animator = null;
  };

  const INTRO_WIGGLE_LEG_MS = 300;

  const playIntroDemo = (): Promise<void> => {
    if (prefersReducedMotion() || parkedAfterExit.value || exiting.value) {
      return Promise.resolve();
    }
    stopAnimator();
    refreshThresholds(false);
    const legs: [Record<string, number>, Record<string, number>][] = [
      [{ dx: 0, dy: 0, rotate: 0 }, { dx: 24, dy: 0, rotate: 4 }],
      [{ dx: 24, dy: 0, rotate: 4 }, { dx: -24, dy: 0, rotate: -4 }],
      [{ dx: -24, dy: 0, rotate: -4 }, { dx: 0, dy: 0, rotate: 0 }],
    ];
    return new Promise((resolve) => {
      introDemoFinish = resolve;
      introDemoActive = true;
      motionActive.value = true;
      const runLeg = (index: number) => {
        if (!introDemoActive) {
          return;
        }
        if (index >= legs.length) {
          finishIntroDemo();
          return;
        }
        const [from, to] = legs[index]!;
        animator = animateDiscoverScalars(
          from,
          to,
          "spring",
          (frame) => {
            dx.value = frame.dx ?? 0;
            dy.value = frame.dy ?? 0;
            rotate.value = frame.rotate ?? 0;
            syncDragProgress();
          },
          () => {
            animator = null;
            if (!introDemoActive) {
              return;
            }
            runLeg(index + 1);
          },
          INTRO_WIGGLE_LEG_MS,
        );
      };
      runLeg(0);
    });
  };

  const runExit = (gesture: DiscoverGesture) => {
    stopAnimator();
    exiting.value = true;
    motionActive.value = true;
    const vw = typeof window !== "undefined" ? window.innerWidth : 400;
    const vh = typeof window !== "undefined" ? window.innerHeight : 700;
    const target = discoverExitTarget(dx.value, dy.value, gesture, vw, vh, velocityX * 1000, velocityY * 1000);
    const from = {
      dx: dx.value,
      dy: dy.value,
      rotate: rotate.value,
      opacity: opacity.value,
    };
    const to = {
      dx: target.x,
      dy: target.y,
      rotate: target.rotate,
      opacity: target.opacity,
    };
    animator = animateDiscoverScalars(
      from,
      to,
      "exit",
      (frame) => {
        dx.value = frame.dx ?? 0;
        dy.value = frame.dy ?? 0;
        rotate.value = frame.rotate ?? 0;
        opacity.value = frame.opacity ?? 1;
        dragProgress.value = 1;
      },
      () => {
        settleAfterExit();
        onGesture(gesture);
      },
    );
  };

  const releaseCapture = (event: PointerEvent) => {
    if (rootEl && typeof rootEl.releasePointerCapture === "function" && rootEl.hasPointerCapture?.(event.pointerId)) {
      rootEl.releasePointerCapture(event.pointerId);
    }
  };

  const onPointerDown = (event: PointerEvent) => {
    if (!rootEl || event.button !== 0 || exiting.value || parkedAfterExit.value) {
      return;
    }
    if (motionActive.value && !introDemoActive) {
      return;
    }
    stopAnimator();
    resetVisual();
    const isTouch = event.pointerType === "touch";
    touchDrag.value = isTouch;
    refreshThresholds(isTouch);
    active = true;
    dragging.value = true;
    startX = event.clientX;
    startY = event.clientY;
    lastMoveX = event.clientX;
    lastMoveY = event.clientY;
    lastMoveT = performance.now();
    velocityX = 0;
    velocityY = 0;
    if (typeof rootEl.setPointerCapture === "function") {
      rootEl.setPointerCapture(event.pointerId);
    }
  };

  const onPointerMove = (event: PointerEvent) => {
    if (!active) {
      return;
    }
    pendingMove = event;
    if (!moveRaf) {
      moveRaf = requestAnimationFrame(flushMove);
    }
  };

  const onPointerUp = (event: PointerEvent) => {
    if (!active) {
      return;
    }
    active = false;
    dragging.value = false;
    if (moveRaf) {
      cancelAnimationFrame(moveRaf);
      moveRaf = 0;
    }
    if (pendingMove) {
      applyMove(pendingMove.clientX, pendingMove.clientY);
      pendingMove = null;
    }
    const gesture = resolveDiscoverGesture(dx.value, dy.value, activeThresholds);
    releaseCapture(event);
    if (!gesture) {
      if (dx.value !== 0 || dy.value !== 0) {
        springBack();
      } else {
        resetVisual();
      }
      return;
    }
    if (gesture === "sheet") {
      springBack();
      onGesture(gesture);
      return;
    }
    runExit(gesture);
  };

  const onPointerCancel = (event: PointerEvent) => {
    if (!active) {
      return;
    }
    active = false;
    dragging.value = false;
    touchDrag.value = false;
    if (moveRaf) {
      cancelAnimationFrame(moveRaf);
      moveRaf = 0;
    }
    pendingMove = null;
    releaseCapture(event);
    if (dx.value !== 0 || dy.value !== 0) {
      springBack();
    } else {
      resetVisual();
    }
  };

  const bind = (root: HTMLElement) => {
    unbind();
    rootEl = root;
    refreshThresholds(false);
    root.addEventListener("pointerdown", onPointerDown);
    root.addEventListener("pointermove", onPointerMove);
    root.addEventListener("pointerup", onPointerUp);
    root.addEventListener("pointercancel", onPointerCancel);
  };

  const unbind = () => {
    if (!rootEl) {
      return;
    }
    stopAnimator();
    if (moveRaf) {
      cancelAnimationFrame(moveRaf);
      moveRaf = 0;
    }
    rootEl.removeEventListener("pointerdown", onPointerDown);
    rootEl.removeEventListener("pointermove", onPointerMove);
    rootEl.removeEventListener("pointerup", onPointerUp);
    rootEl.removeEventListener("pointercancel", onPointerCancel);
    rootEl = null;
    active = false;
    dragging.value = false;
    pendingMove = null;
    resetVisual();
  };

  onUnmounted(unbind);

  return {
    dx,
    dy,
    rotate,
    opacity,
    dragging,
    exiting,
    parkedAfterExit,
    motionActive,
    dragProgress,
    touchDrag,
    gestureThresholds,
    clearParkedSilent,
    recoverFromParked,
    playIntroDemo,
    bind,
    unbind,
  };
}