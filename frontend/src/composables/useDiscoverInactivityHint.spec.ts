import { defineComponent, ref } from "vue";
import { flushPromises, mount } from "@vue/test-utils";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { setDiscoverReducedMotionOverride } from "../discoverMotion";
import {
  DISCOVER_INACTIVITY_HINT_MS,
  DISCOVER_INACTIVITY_MAX_HINTS,
  useDiscoverInactivityHint,
} from "./useDiscoverInactivityHint";

function mountHint(playIntroDemo = vi.fn(() => Promise.resolve())) {
  const helpOpen = ref(false);
  const filterOpen = ref(false);
  const sheetPresent = ref(false);
  const quickOpen = ref(false);
  const followBusy = ref(false);
  const loading = ref(false);
  const currentCardKey = ref<string | null>("film:1");
  const dragging = ref(false);
  const exiting = ref(false);
  const parkedAfterExit = ref(false);
  const motionActive = ref(false);

  const Host = defineComponent({
    setup() {
      const controls = useDiscoverInactivityHint({
        helpOpen,
        filterOpen,
        sheetPresent,
        quickOpen,
        followBusy,
        loading,
        currentCardKey,
        dragging,
        exiting,
        parkedAfterExit,
        motionActive,
        playIntroDemo,
      });
      return { controls, helpOpen, sheetPresent, currentCardKey };
    },
    template: "<div />",
  });

  const wrapper = mount(Host);
  return {
    wrapper,
    playIntroDemo,
    helpOpen,
    sheetPresent,
    currentCardKey,
    controls: wrapper.vm.controls as ReturnType<typeof useDiscoverInactivityHint>,
  };
}

describe("useDiscoverInactivityHint", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    setDiscoverReducedMotionOverride(false);
    Object.defineProperty(document, "visibilityState", { configurable: true, value: "visible" });
  });

  afterEach(() => {
    vi.useRealTimers();
    setDiscoverReducedMotionOverride(null);
    vi.restoreAllMocks();
  });

  it("relance après 6 s d’inactivité", async () => {
    const playIntroDemo = vi.fn(() => Promise.resolve());
    mountHint(playIntroDemo);
    await vi.advanceTimersByTimeAsync(DISCOVER_INACTIVITY_HINT_MS - 1);
    expect(playIntroDemo).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(1);
    await flushPromises();
    expect(playIntroDemo).toHaveBeenCalledTimes(1);
  });

  it("ne relance pas tant que l’aide ou la fiche est ouverte", async () => {
    const playIntroDemo = vi.fn(() => Promise.resolve());
    const ctx = mountHint(playIntroDemo);
    ctx.helpOpen.value = true;
    await vi.advanceTimersByTimeAsync(DISCOVER_INACTIVITY_HINT_MS);
    expect(playIntroDemo).not.toHaveBeenCalled();
    ctx.helpOpen.value = false;
    await flushPromises();
    await vi.advanceTimersByTimeAsync(DISCOVER_INACTIVITY_HINT_MS);
    await flushPromises();
    expect(playIntroDemo).toHaveBeenCalledTimes(1);

    playIntroDemo.mockClear();
    ctx.sheetPresent.value = true;
    await vi.advanceTimersByTimeAsync(DISCOVER_INACTIVITY_HINT_MS);
    expect(playIntroDemo).not.toHaveBeenCalled();
  });

  it("un pointerdown remet le délai à zéro", async () => {
    const playIntroDemo = vi.fn(() => Promise.resolve());
    const ctx = mountHint(playIntroDemo);
    await vi.advanceTimersByTimeAsync(5000);
    ctx.controls.bump();
    await vi.advanceTimersByTimeAsync(5000);
    expect(playIntroDemo).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(1000);
    await flushPromises();
    expect(playIntroDemo).toHaveBeenCalledTimes(1);
  });

  it("replanifie après résolution anticipée de playIntroDemo", async () => {
    let finish: (() => void) | null = null;
    const playIntroDemo = vi.fn(
      () =>
        new Promise<void>((resolve) => {
          finish = resolve;
        }),
    );
    mountHint(playIntroDemo);
    await vi.advanceTimersByTimeAsync(DISCOVER_INACTIVITY_HINT_MS);
    await flushPromises();
    expect(playIntroDemo).toHaveBeenCalledTimes(1);
    finish?.();
    await flushPromises();
    await vi.advanceTimersByTimeAsync(DISCOVER_INACTIVITY_HINT_MS);
    await flushPromises();
    expect(playIntroDemo).toHaveBeenCalledTimes(2);
  });

  it(`plafonne à ${DISCOVER_INACTIVITY_MAX_HINTS} relances par carte`, async () => {
    const playIntroDemo = vi.fn(() => Promise.resolve());
    mountHint(playIntroDemo);
    for (let i = 0; i < DISCOVER_INACTIVITY_MAX_HINTS; i += 1) {
      await vi.advanceTimersByTimeAsync(DISCOVER_INACTIVITY_HINT_MS);
      await flushPromises();
    }
    expect(playIntroDemo).toHaveBeenCalledTimes(DISCOVER_INACTIVITY_MAX_HINTS);
    await vi.advanceTimersByTimeAsync(DISCOVER_INACTIVITY_HINT_MS);
    await flushPromises();
    expect(playIntroDemo).toHaveBeenCalledTimes(DISCOVER_INACTIVITY_MAX_HINTS);
  });
});