import type { DiscoverGesture } from "./discoverTypes";

export const DISCOVER_MOUSE_THRESHOLD = 72;

export interface DiscoverGestureThresholds {
  horizontal: number;
  vertical: number;
}

export type DiscoverEdgeSide = "left" | "right" | "top" | "bottom";

export const DISCOVER_EDGE_ACTION_LABELS: Record<DiscoverEdgeSide, string> = {
  right: "Suivre",
  left: "Pas intéressé",
  top: "Fiche",
  bottom: "Plus tard",
};

export function discoverGestureThresholds(
  cardWidth: number,
  cardHeight: number,
  touch: boolean,
): DiscoverGestureThresholds {
  if (!touch) {
    return { horizontal: DISCOVER_MOUSE_THRESHOLD, vertical: DISCOVER_MOUSE_THRESHOLD };
  }
  return {
    horizontal: Math.max(120, cardWidth * 0.28),
    vertical: Math.max(120, cardHeight * 0.2),
  };
}

export function resolveDiscoverGesture(
  dx: number,
  dy: number,
  thresholds: DiscoverGestureThresholds | number = DISCOVER_MOUSE_THRESHOLD,
): DiscoverGesture | null {
  const thX = typeof thresholds === "number" ? thresholds : thresholds.horizontal;
  const thY = typeof thresholds === "number" ? thresholds : thresholds.vertical;
  const ax = Math.abs(dx);
  const ay = Math.abs(dy);
  if (ax < thX && ay < thY) {
    return null;
  }
  if (ax >= ay) {
    if (ax < thX) {
      return null;
    }
    return dx > 0 ? "follow" : "pass";
  }
  if (ay < thY) {
    return null;
  }
  return dy < 0 ? "sheet" : "later";
}

export function discoverDominantEdge(dx: number, dy: number): DiscoverEdgeSide | null {
  const ax = Math.abs(dx);
  const ay = Math.abs(dy);
  if (ax < 1 && ay < 1) {
    return null;
  }
  if (ax >= ay) {
    return dx > 0 ? "right" : "left";
  }
  return dy < 0 ? "top" : "bottom";
}

export function discoverEdgeActionLabel(side: DiscoverEdgeSide): string {
  return DISCOVER_EDGE_ACTION_LABELS[side];
}

export function discoverEdgeReleaseHint(
  dx: number,
  dy: number,
  thresholds: DiscoverGestureThresholds,
): { side: DiscoverEdgeSide; actionLabel: string; atThreshold: boolean; hint: string } | null {
  const side = discoverDominantEdge(dx, dy);
  if (!side) {
    return null;
  }
  const gesture = resolveDiscoverGesture(dx, dy, thresholds);
  const atThreshold = gesture !== null;
  const actionLabel = discoverEdgeActionLabel(side);
  const hint = atThreshold ? `Relâcher pour ${actionLabel}` : "Relâcher pour annuler";
  return { side, actionLabel, atThreshold, hint };
}

export function discoverFollowStampVisible(
  dx: number,
  dy: number,
  thresholds: DiscoverGestureThresholds,
): boolean {
  return resolveDiscoverGesture(dx, dy, thresholds) === "follow";
}