import { mount } from "@vue/test-utils";
import { nextTick } from "vue";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import ActionMenuPopover from "./ActionMenuPopover.vue";

describe("ActionMenuPopover", () => {
  beforeEach(() => {
    vi.stubGlobal("innerWidth", 390);
    vi.stubGlobal("innerHeight", 844);
    Object.defineProperty(document.documentElement, "clientWidth", {
      configurable: true,
      value: 390,
    });
  });

  afterEach(() => {
    document.body.innerHTML = "";
    vi.unstubAllGlobals();
  });

  it("tient compte de la barre mobile pour le positionnement vertical", async () => {
    const tabbar = document.createElement("nav");
    tabbar.className = "mobile-tabbar";
    document.body.append(tabbar);
    vi.spyOn(tabbar, "getBoundingClientRect").mockReturnValue({
      x: 0,
      y: 780,
      width: 390,
      height: 64,
      top: 780,
      right: 390,
      bottom: 844,
      left: 0,
      toJSON: () => ({}),
    });
    vi.spyOn(window, "getComputedStyle").mockImplementation((el) => {
      if (el === tabbar) {
        return { display: "grid" } as CSSStyleDeclaration;
      }
      return { display: "block" } as CSSStyleDeclaration;
    });

    const wrapper = mount(ActionMenuPopover, {
      props: { triggerVariant: "fiche", compactOnPhone: true, label: "Actions" },
      slots: {
        default:
          '<button type="button" role="menuitem">Première action</button>' +
          '<button type="button" role="menuitem">Deuxième action</button>' +
          '<button type="button" role="menuitem">Troisième action</button>',
      },
      attachTo: document.body,
      global: { stubs: { StatusIcon: true } },
    });

    const trigger = wrapper.get("button.action-menu-trigger");
    vi.spyOn(trigger.element, "getBoundingClientRect").mockReturnValue({
      x: 320,
      y: 700,
      width: 44,
      height: 44,
      top: 700,
      right: 364,
      bottom: 744,
      left: 320,
      toJSON: () => ({}),
    });

    await trigger.trigger("click");
    await nextTick();
    await new Promise((resolve) => requestAnimationFrame(() => resolve(undefined)));

    const menu = document.body.querySelector<HTMLElement>(".action-menu-popover");
    expect(menu).toBeTruthy();
    const top = Number.parseFloat(menu!.style.top);
    const maxHeight = Number.parseFloat(menu!.style.maxHeight || "0");
    const renderedHeight = maxHeight > 0 ? maxHeight : menu!.offsetHeight;
    expect(top + renderedHeight).toBeLessThanOrEqual(780 - 8 + 1);
  });

  it("reste dans le viewport sur bureau (1280px)", async () => {
    vi.stubGlobal("innerWidth", 1280);
    vi.stubGlobal("innerHeight", 800);
    Object.defineProperty(document.documentElement, "clientWidth", {
      configurable: true,
      value: 1280,
    });

    const items = Array.from({ length: 8 }, (_, i) => `<button type="button" role="menuitem">Entrée ${i + 1}</button>`).join(
      "",
    );

    const wrapper = mount(ActionMenuPopover, {
      props: { triggerVariant: "toggle", label: "Plus d'actions" },
      slots: { default: items },
      attachTo: document.body,
      global: { stubs: { StatusIcon: true } },
    });

    const trigger = wrapper.get("button.action-menu-trigger");
    vi.spyOn(trigger.element, "getBoundingClientRect").mockReturnValue({
      x: 900,
      y: 710,
      width: 160,
      height: 40,
      top: 710,
      right: 1060,
      bottom: 750,
      left: 900,
      toJSON: () => ({}),
    });

    await trigger.trigger("click");
    await nextTick();
    await new Promise((resolve) => requestAnimationFrame(() => resolve(undefined)));

    const menu = document.body.querySelector<HTMLElement>(".action-menu-popover");
    expect(menu).toBeTruthy();
    const top = Number.parseFloat(menu!.style.top);
    const maxHeight = Number.parseFloat(menu!.style.maxHeight || "0");
    const renderedHeight = maxHeight > 0 ? maxHeight : menu!.offsetHeight;
    expect(top + renderedHeight).toBeLessThanOrEqual(800 - 8 + 1);
  });
});