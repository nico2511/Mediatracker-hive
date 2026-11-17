import type { DiscoverGestureThresholds } from "./discoverGestures";
import { DISCOVER_MOUSE_THRESHOLD } from "./discoverGestures";

export type DiscoverMotionCurve = "exit" | "spring" | "sheet";

export const DISCOVER_MOTION_MS: Record<DiscoverMotionCurve, number> = {
  exit: 300,
  spring: 280,
  sheet: 260,
};

/** Approximate cubic-bezier easing (0–1 progress in/out). */
export function cubicBezierY(t: number, x1: number, y1: number, x2: number, y2: number): number {
  if (t <= 0) {
    return 0;
  }
  if (t >= 1) {
    return 1;
  }
  let start = 0;
  let end = 1;
  for (let i = 0; i < 12; i += 1) {
    const mid = (start + end) / 2;
    const x = bezierComponent(mid, x1, x2);
    if (x < t) {
      start = mid;
    } else {
      end = mid;
    }
  }
  const u = (start + end) / 2;
  return bezierComponent(u, y1, y2);
}

function bezierComponent(t: number, a: number, b: number): number {
  const c = 1 - t;
  return 3 * c * c * t * a + 3 * c * t * t * b + t * t * t;
}

const CURVES: Record<DiscoverMotionCurve, [number, number, number, number]> = {
  exit: [0.32, 0.72, 0, 1],
  spring: [0.34, 1.22, 0.64, 1],
  sheet: [0.22, 1, 0.36, 1],
};

export function discoverMotionDuration(curve: DiscoverMotionCurve): number {
  if (prefersReducedMotion()) {
    return 0;
  }
  return DISCOVER_MOTION_MS[curve];
}

let reducedMotionOverride: boolean | null = null;

/** Tests uniquement : forcer ou lever la réduction de mouvement. */
export function setDiscoverReducedMotionOverride(value: boolean | null): void {
  reducedMotionOverride = value;
}

export function prefersReducedMotion(): boolean {
  if (reducedMotionOverride !== null) {
    return reducedMotionOverride;
  }
  if (import.meta.env.VITEST) {
    return true;
  }
  if (typeof window === "undefined" || typeof window.matchMedia !== "function") {
    return false;
  }
  return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

export function discoverEase(t: number, curve: DiscoverMotionCurve): number {
  const [x1, y1, x2, y2] = CURVES[curve];
  return cubicBezierY(t, x1, y1, x2, y2);
}

export interface DiscoverRafAnimator {
  cancel: () => void;
}

export function animateDiscoverScalar(
  from: number,
  to: number,
  curve: DiscoverMotionCurve,
  onFrame: (value: number) => void,
  onDone?: () => void,
): DiscoverRafAnimator {
  const duration = discoverMotionDuration(curve);
  if (duration <= 0) {
    onFrame(to);
    onDone?.();
    return { cancel: () => {} };
  }

  let rafId = 0;
  const t0 = performance.now();
  let cancelled = false;

  const tick = (now: number) => {
    if (cancelled) {
      return;
    }
    const raw = Math.min(1, (now - t0) / duration);
    const eased = discoverEase(raw, curve);
    onFrame(from + (to - from) * eased);
    if (raw < 1) {
      rafId = requestAnimationFrame(tick);
    } else {
      onDone?.();
    }
  };

  rafId = requestAnimationFrame(tick);
  return {
    cancel: () => {
      cancelled = true;
      cancelAnimationFrame(rafId);
    },
  };
}

export function animateDiscoverScalars(
  from: Record<string, number>,
  to: Record<string, number>,
  curve: DiscoverMotionCurve,
  onFrame: (values: Record<string, number>) => void,
  onDone?: () => void,
  durationOverrideMs?: number,
): DiscoverRafAnimator {
  const keys = Object.keys(to);
  const duration =
    durationOverrideMs !== undefined ? (prefersReducedMotion() ? 0 : durationOverrideMs) : discoverMotionDuration(curve);
  if (duration <= 0) {
    onFrame({ ...to });
    onDone?.();
    return { cancel: () => {} };
  }

  let rafId = 0;
  const t0 = performance.now();
  let cancelled = false;

  const tick = (now: number) => {
    if (cancelled) {
      return;
    }
    const raw = Math.min(1, (now - t0) / duration);
    const eased = discoverEase(raw, curve);
    const frame: Record<string, number> = {};
    for (const key of keys) {
      const a = from[key] ?? 0;
      const b = to[key] ?? 0;
      frame[key] = a + (b - a) * eased;
    }
    onFrame(frame);
    if (raw < 1) {
      rafId = requestAnimationFrame(tick);
    } else {
      onDone?.();
    }
  };

  rafId = requestAnimationFrame(tick);
  return {
    cancel: () => {
      cancelled = true;
      cancelAnimationFrame(rafId);
    },
  };
}

export function discoverDragProgress(
  dx: number,
  dy: number,
  thresholds: DiscoverGestureThresholds | number = DISCOVER_MOUSE_THRESHOLD,
): number {
  const thX = typeof thresholds === "number" ? thresholds : thresholds.horizontal;
  const thY = typeof thresholds === "number" ? thresholds : thresholds.vertical;
  const ax = Math.abs(dx);
  const ay = Math.abs(dy);
  const mag = Math.max(ax, ay);
  const th = ax >= ay ? thX : thY;
  return Math.min(1, mag / th);
}

export function discoverCardTilt(dx: number): number {
  return Math.max(-18, Math.min(18, dx / 12));
}

export function discoverExitTarget(
  dx: number,
  dy: number,
  gesture: "follow" | "pass" | "sheet" | "later",
  viewportW: number,
  viewportH: number,
  velocityX = 0,
  velocityY = 0,
): { x: number; y: number; rotate: number; opacity: number } {
  const boostX = velocityX * 0.12;
  const boostY = velocityY * 0.12;
  const margin = 120;
  let x = dx + boostX;
  let y = dy + boostY;
  let rotate = discoverCardTilt(dx + boostX);

  if (gesture === "follow") {
    x = viewportW + margin;
    rotate = Math.max(rotate, 8);
  } else if (gesture === "pass") {
    x = -(viewportW + margin);
    rotate = Math.min(rotate, -8);
  } else if (gesture === "later") {
    y = viewportH + margin;
    rotate = rotate * 0.35;
  } else {
    y = -(viewportH + margin);
    rotate = rotate * 0.35;
  }

  return { x, y, rotate, opacity: 0 };
}

export function underCardTransform(progress: number): { scale: number; translateY: number } {
  const p = Math.max(0, Math.min(1, progress));
  const scale = 0.93 + p * 0.07;
  const translateY = 18 * (1 - p);
  return { scale, translateY };
}