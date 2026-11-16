import type { DiscoverGesture } from "./discoverTypes";

const DEFAULT_THRESHOLD = 72;

export function resolveDiscoverGesture(
  dx: number,
  dy: number,
  threshold = DEFAULT_THRESHOLD,
): DiscoverGesture | null {
  const ax = Math.abs(dx);
  const ay = Math.abs(dy);
  if (ax < threshold && ay < threshold) {
    return null;
  }
  if (ax >= ay) {
    return dx > 0 ? "follow" : "pass";
  }
  return dy < 0 ? "sheet" : "later";
}