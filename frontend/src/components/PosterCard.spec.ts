import { flushPromises, mount } from "@vue/test-utils";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { LONG_PRESS_MS } from "../composables/useLongPress";
import PosterCard from "./PosterCard.vue";

vi.mock("vue-router", () => ({
  useRouter: () => ({ push: vi.fn() }),
}));

describe("PosterCard appui long mobile", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("le contextmenu sur le fond ne ferme pas le menu juste après l'appui long", async () => {
    const w = mount(
      PosterCard,
      {
        props: { title: "Titre fictif", kindLabel: "Film" },
        attachTo: document.body,
        global: { stubs: { TmdbPosterImg: true, StatusIcon: true, RouterLink: true } },
      },
    );

    const shell = w.find(".pc-shell");
    await shell.trigger("pointerdown", { pointerId: 1, pointerType: "touch", isPrimary: true, clientX: 40, clientY: 40 });
    vi.advanceTimersByTime(LONG_PRESS_MS);
    await flushPromises();

    expect(document.body.querySelector(".pc-menu-backdrop")).toBeTruthy();

    const backdrop = document.body.querySelector(".pc-menu-backdrop") as HTMLElement;
    backdrop.dispatchEvent(new MouseEvent("contextmenu", { bubbles: true, cancelable: true }));
    await flushPromises();

    expect(document.body.querySelector(".pc-menu")).toBeTruthy();

    vi.advanceTimersByTime(900);
    backdrop.dispatchEvent(new MouseEvent("contextmenu", { bubbles: true, cancelable: true }));
    await flushPromises();

    expect(document.body.querySelector(".pc-menu")).toBeFalsy();
    w.unmount();
  });
});
