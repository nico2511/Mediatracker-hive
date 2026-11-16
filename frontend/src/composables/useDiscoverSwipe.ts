import { onUnmounted, ref, type Ref } from "vue";

import { resolveDiscoverGesture } from "../discoverGestures";
import type { DiscoverGesture } from "../discoverTypes";

export interface DiscoverSwipeState {
  dx: Ref<number>;
  dy: Ref<number>;
  dragging: Ref<boolean>;
  bind: (root: HTMLElement) => void;
  unbind: () => void;
}

export function useDiscoverSwipe(onGesture: (gesture: DiscoverGesture) => void): DiscoverSwipeState {
  const dx = ref(0);
  const dy = ref(0);
  const dragging = ref(false);
  let startX = 0;
  let startY = 0;
  let active = false;
  let rootEl: HTMLElement | null = null;

  const onPointerDown = (event: PointerEvent) => {
    if (!rootEl || event.button !== 0) {
      return;
    }
    active = true;
    dragging.value = true;
    startX = event.clientX;
    startY = event.clientY;
    dx.value = 0;
    dy.value = 0;
    if (typeof rootEl.setPointerCapture === "function") {
      rootEl.setPointerCapture(event.pointerId);
    }
  };

  const onPointerMove = (event: PointerEvent) => {
    if (!active) {
      return;
    }
    dx.value = event.clientX - startX;
    dy.value = event.clientY - startY;
  };

  const onPointerUp = (event: PointerEvent) => {
    if (!active) {
      return;
    }
    active = false;
    dragging.value = false;
    const gesture = resolveDiscoverGesture(dx.value, dy.value);
    dx.value = 0;
    dy.value = 0;
    if (rootEl && typeof rootEl.releasePointerCapture === "function" && rootEl.hasPointerCapture?.(event.pointerId)) {
      rootEl.releasePointerCapture(event.pointerId);
    }
    if (gesture) {
      onGesture(gesture);
    }
  };

  const bind = (root: HTMLElement) => {
    unbind();
    rootEl = root;
    root.addEventListener("pointerdown", onPointerDown);
    root.addEventListener("pointermove", onPointerMove);
    root.addEventListener("pointerup", onPointerUp);
    root.addEventListener("pointercancel", onPointerUp);
  };

  const unbind = () => {
    if (!rootEl) {
      return;
    }
    rootEl.removeEventListener("pointerdown", onPointerDown);
    rootEl.removeEventListener("pointermove", onPointerMove);
    rootEl.removeEventListener("pointerup", onPointerUp);
    rootEl.removeEventListener("pointercancel", onPointerUp);
    rootEl = null;
    active = false;
    dragging.value = false;
  };

  onUnmounted(unbind);

  return { dx, dy, dragging, bind, unbind };
}