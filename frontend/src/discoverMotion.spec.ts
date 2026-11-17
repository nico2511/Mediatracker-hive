import { afterEach, describe, expect, it, vi } from "vitest";

import {
  discoverDragProgress,
  discoverEase,
  discoverMotionDuration,
  prefersReducedMotion,
  setDiscoverReducedMotionOverride,
  underCardTransform,
} from "./discoverMotion";

describe("discoverMotion", () => {
  afterEach(() => {
    setDiscoverReducedMotionOverride(null);
    vi.restoreAllMocks();
  });

  it("raccourcit les durées sous Vitest (mouvement réduit implicite)", () => {
    expect(prefersReducedMotion()).toBe(true);
    expect(discoverMotionDuration("exit")).toBe(0);
    expect(discoverMotionDuration("spring")).toBe(0);
  });

  it("applique les durées complètes quand le mouvement n’est pas réduit", () => {
    setDiscoverReducedMotionOverride(false);
    expect(prefersReducedMotion()).toBe(false);
    expect(discoverMotionDuration("exit")).toBe(300);
    expect(discoverMotionDuration("spring")).toBe(280);
    expect(discoverMotionDuration("sheet")).toBe(260);
  });

  it("interpole la carte du dessous selon la progression", () => {
    const peek = underCardTransform(0);
    expect(peek.scale).toBeCloseTo(0.93);
    expect(peek.translateY).toBeCloseTo(18);
    const full = underCardTransform(1);
    expect(full.scale).toBeCloseTo(1);
    expect(full.translateY).toBeCloseTo(0);
  });

  it("calcule une progression de drag bornée", () => {
    expect(discoverDragProgress(36, 0)).toBeCloseTo(0.5);
    expect(discoverDragProgress(200, 0)).toBe(1);
  });

  it("fournit une courbe de sortie monotone", () => {
    expect(discoverEase(0, "exit")).toBeCloseTo(0);
    expect(discoverEase(1, "exit")).toBeCloseTo(1);
    expect(discoverEase(0.5, "exit")).toBeGreaterThan(0.3);
  });
});