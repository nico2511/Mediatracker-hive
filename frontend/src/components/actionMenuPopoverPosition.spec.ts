import { describe, expect, it } from "vitest";

import {
  ACTION_MENU_GAP,
  ACTION_MENU_VIEWPORT_MARGIN,
  computeHorizontalPosition,
  computeVerticalPosition,
  viewportBottomLimit,
} from "./actionMenuPopoverPosition";

describe("actionMenuPopoverPosition", () => {
  it("réserve l'espace au-dessus de la barre mobile", () => {
    expect(viewportBottomLimit(800, 740)).toBe(732);
    expect(viewportBottomLimit(800, null)).toBe(792);
  });

  it("ouvre le menu vers le bas quand la hauteur suffit", () => {
    const trigger = { top: 400, bottom: 430, left: 0, right: 40, width: 40 };
    const result = computeVerticalPosition(trigger, 120, 700);
    expect(result.top).toBe(`${430 + ACTION_MENU_GAP}px`);
    expect(result.maxHeight).toBeUndefined();
  });

  it("ouvre vers le haut quand le bas est trop court et le haut suffit", () => {
    const trigger = { top: 500, bottom: 530, left: 0, right: 40, width: 40 };
    const viewportBottom = 560;
    const menuHeight = 100;
    const spaceBelow = viewportBottom - (trigger.bottom + ACTION_MENU_GAP);
    expect(menuHeight).toBeGreaterThan(spaceBelow);
    const result = computeVerticalPosition(trigger, menuHeight, viewportBottom);
    expect(result.top).toBe(`${trigger.top - ACTION_MENU_GAP - menuHeight}px`);
  });

  it("borne et rend défilable quand ni le haut ni le bas ne suffisent (390px)", () => {
    const trigger = { top: 400, bottom: 430, left: 300, right: 340, width: 40 };
    const viewportBottom = viewportBottomLimit(844, 780);
    const menuHeight = 400;
    const result = computeVerticalPosition(trigger, menuHeight, viewportBottom);
    expect(result.maxHeight).toBeDefined();
    expect(result.overflowY).toBe("auto");
    const maxHeight = Number.parseFloat(result.maxHeight!);
    const top = Number.parseFloat(result.top);
    expect(maxHeight).toBeGreaterThan(0);
    expect(top + maxHeight).toBeLessThanOrEqual(viewportBottom + 1);
  });

  it("aligne à droite sur viewport large (1280px)", () => {
    const trigger = { top: 200, bottom: 230, left: 1100, right: 1140, width: 40 };
    const menuWidth = 250;
    const { left, minWidth } = computeHorizontalPosition(trigger, menuWidth, 1280, menuWidth);
    expect(left).toBe(trigger.right - menuWidth);
    expect(minWidth).toBe(menuWidth);
  });
});