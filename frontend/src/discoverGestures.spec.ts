import { describe, expect, it } from "vitest";

import {
  discoverEdgeReleaseHint,
  discoverFollowStampVisible,
  discoverGestureThresholds,
  resolveDiscoverGesture,
} from "./discoverGestures";

describe("discoverGestureThresholds", () => {
  it("garde 72 px pour la souris", () => {
    expect(discoverGestureThresholds(320, 640, false)).toEqual({ horizontal: 72, vertical: 72 });
  });

  it("calcule des seuils relatifs au tactile", () => {
    expect(discoverGestureThresholds(400, 700, true)).toEqual({ horizontal: 120, vertical: 140 });
    expect(discoverGestureThresholds(500, 800, true)).toEqual({ horizontal: 140, vertical: 160 });
  });
});

describe("resolveDiscoverGesture", () => {
  const mouse = { horizontal: 72, vertical: 72 };
  const touchWide = discoverGestureThresholds(400, 700, true);

  it("résout les quatre gestes à la souris", () => {
    expect(resolveDiscoverGesture(120, 0, mouse)).toBe("follow");
    expect(resolveDiscoverGesture(-120, 0, mouse)).toBe("pass");
    expect(resolveDiscoverGesture(0, -120, mouse)).toBe("sheet");
    expect(resolveDiscoverGesture(0, 120, mouse)).toBe("later");
  });

  it("ignore les mouvements faibles à la souris", () => {
    expect(resolveDiscoverGesture(10, 10, mouse)).toBeNull();
    expect(resolveDiscoverGesture(71, 0, mouse)).toBeNull();
  });

  it("exige le seuil tactile horizontal", () => {
    expect(resolveDiscoverGesture(119, 0, touchWide)).toBeNull();
    expect(resolveDiscoverGesture(120, 0, touchWide)).toBe("follow");
  });

  it("exige le seuil tactile vertical", () => {
    expect(resolveDiscoverGesture(0, 139, touchWide)).toBeNull();
    expect(resolveDiscoverGesture(0, 140, touchWide)).toBe("later");
  });
});

describe("discoverEdgeReleaseHint", () => {
  const touch = discoverGestureThresholds(400, 700, true);

  it("propose le libellé du bord et l’indication au seuil", () => {
    const ready = discoverEdgeReleaseHint(140, 0, touch);
    expect(ready).toMatchObject({ side: "right", actionLabel: "Suivre", atThreshold: true });
    expect(ready?.hint).toBe("Relâcher pour Suivre");

    const sheet = discoverEdgeReleaseHint(0, -150, touch);
    expect(sheet).toMatchObject({ side: "top", actionLabel: "Fiche", atThreshold: true });

    const pass = discoverEdgeReleaseHint(-130, 10, touch);
    expect(pass).toMatchObject({ side: "left", actionLabel: "Pas intéressé", atThreshold: true });

    const later = discoverEdgeReleaseHint(5, 145, touch);
    expect(later).toMatchObject({ side: "bottom", actionLabel: "Plus tard", atThreshold: true });
  });

  it("estompe sous le seuil", () => {
    const hint = discoverEdgeReleaseHint(60, 0, touch);
    expect(hint?.atThreshold).toBe(false);
    expect(hint?.hint).toBe("Relâcher pour annuler");
  });
});

describe("discoverFollowStampVisible", () => {
  const mouse = { horizontal: 72, vertical: 72 };
  const touch = discoverGestureThresholds(400, 700, true);

  it("n’apparaît qu’au seuil de validation Suivre (souris)", () => {
    expect(discoverFollowStampVisible(71, 0, mouse)).toBe(false);
    expect(discoverFollowStampVisible(72, 0, mouse)).toBe(true);
  });

  it("n’apparaît qu’au seuil de validation Suivre (tactile)", () => {
    expect(discoverFollowStampVisible(119, 0, touch)).toBe(false);
    expect(discoverFollowStampVisible(120, 0, touch)).toBe(true);
  });
});