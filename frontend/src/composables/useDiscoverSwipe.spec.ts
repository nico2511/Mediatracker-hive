import { defineComponent, onMounted, ref } from "vue";
import { flushPromises, mount } from "@vue/test-utils";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { setDiscoverReducedMotionOverride } from "../discoverMotion";
import { useDiscoverSwipe } from "./useDiscoverSwipe";

function mountSwipeHost() {
  const onGesture = vi.fn();
  const Host = defineComponent({
    setup() {
      const swipe = useDiscoverSwipe(onGesture);
      const root = ref<HTMLElement | null>(null);
      onMounted(() => {
        if (root.value) {
          swipe.bind(root.value);
        }
      });
      return { swipe, root, onGesture };
    },
    template:
      '<div ref="root" data-card style="touch-action:none;width:400px;height:700px" />',
  });
  return mount(Host);
}

describe("useDiscoverSwipe", () => {
  beforeEach(() => {
    setDiscoverReducedMotionOverride(false);
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
    setDiscoverReducedMotionOverride(null);
    vi.restoreAllMocks();
  });

  it("interrompt le balancement par toucher et démarre le drag", async () => {
    const wrapper = mountSwipeHost();
    await flushPromises();
    const introDone = wrapper.vm.swipe.playIntroDemo();
    await flushPromises();
    const card = wrapper.find("[data-card]");
    await card.trigger("pointerdown", { clientX: 10, clientY: 10, button: 0, pointerId: 1 });
    await card.trigger("pointermove", { clientX: 50, clientY: 10, pointerId: 1 });
    await vi.advanceTimersByTimeAsync(20);
    await flushPromises();
    expect(wrapper.vm.swipe.dragging.value).toBe(true);
    expect(Math.abs(wrapper.vm.swipe.dx.value)).toBeGreaterThan(0);
    await expect(introDone).resolves.toBeUndefined();
    expect(wrapper.vm.swipe.motionActive.value).toBe(false);
  });

  it("résout playIntroDemo après unbind puis permet une nouvelle démo", async () => {
    const wrapper = mountSwipeHost();
    await flushPromises();
    const first = wrapper.vm.swipe.playIntroDemo();
    await flushPromises();
    wrapper.vm.swipe.unbind();
    await expect(first).resolves.toBeUndefined();
    expect(wrapper.vm.swipe.motionActive.value).toBe(false);
    const second = wrapper.vm.swipe.playIntroDemo();
    await vi.runAllTimersAsync();
    await flushPromises();
    await expect(second).resolves.toBeUndefined();
  });

  it("pointercancel n’émet aucun geste", async () => {
    const wrapper = mountSwipeHost();
    await flushPromises();
    const card = wrapper.find("[data-card]");
    await card.trigger("pointerdown", {
      clientX: 0,
      clientY: 0,
      button: 0,
      pointerId: 1,
      pointerType: "touch",
    });
    await card.trigger("pointermove", { clientX: 200, clientY: 0, pointerId: 1, pointerType: "touch" });
    await vi.advanceTimersByTimeAsync(20);
    await card.trigger("pointercancel", { clientX: 200, clientY: 0, pointerId: 1, pointerType: "touch" });
    await flushPromises();
    expect(wrapper.vm.onGesture).not.toHaveBeenCalled();
    expect(wrapper.vm.swipe.dragging.value).toBe(false);
  });

  it("valide le geste tactile uniquement au-delà du seuil relatif", async () => {
    const wrapper = mountSwipeHost();
    await flushPromises();
    const card = wrapper.find("[data-card]");
    await card.trigger("pointerdown", {
      clientX: 0,
      clientY: 0,
      button: 0,
      pointerId: 1,
      pointerType: "touch",
    });
    await card.trigger("pointermove", { clientX: 90, clientY: 0, pointerId: 1, pointerType: "touch" });
    await vi.advanceTimersByTimeAsync(20);
    await card.trigger("pointerup", { clientX: 90, clientY: 0, pointerId: 1, pointerType: "touch" });
    await vi.runAllTimersAsync();
    await flushPromises();
    expect(wrapper.vm.onGesture).not.toHaveBeenCalled();

    await card.trigger("pointerdown", {
      clientX: 0,
      clientY: 0,
      button: 0,
      pointerId: 2,
      pointerType: "touch",
    });
    await card.trigger("pointermove", { clientX: 130, clientY: 0, pointerId: 2, pointerType: "touch" });
    await vi.advanceTimersByTimeAsync(20);
    await card.trigger("pointerup", { clientX: 130, clientY: 0, pointerId: 2, pointerType: "touch" });
    await vi.runAllTimersAsync();
    await flushPromises();
    expect(wrapper.vm.onGesture).toHaveBeenCalledWith("follow");
  });

  it("garde le seuil 72 px pour la souris", async () => {
    const wrapper = mountSwipeHost();
    await flushPromises();
    const card = wrapper.find("[data-card]");
    await card.trigger("pointerdown", { clientX: 0, clientY: 0, button: 0, pointerId: 1, pointerType: "mouse" });
    await card.trigger("pointermove", { clientX: 80, clientY: 0, pointerId: 1, pointerType: "mouse" });
    await vi.advanceTimersByTimeAsync(20);
    await card.trigger("pointerup", { clientX: 80, clientY: 0, pointerId: 1, pointerType: "mouse" });
    await vi.runAllTimersAsync();
    await flushPromises();
    expect(wrapper.vm.onGesture).toHaveBeenCalledWith("follow");
  });

  it("ignore le toucher pendant springBack", async () => {
    const wrapper = mountSwipeHost();
    await flushPromises();
    const card = wrapper.find("[data-card]");
    await card.trigger("pointerdown", { clientX: 0, clientY: 0, button: 0, pointerId: 1 });
    await card.trigger("pointermove", { clientX: 10, clientY: 10, pointerId: 1 });
    await card.trigger("pointerup", { clientX: 10, clientY: 10, pointerId: 1 });
    await flushPromises();
    expect(wrapper.vm.swipe.motionActive.value).toBe(true);
    await card.trigger("pointerdown", { clientX: 0, clientY: 0, button: 0, pointerId: 2 });
    expect(wrapper.vm.swipe.dragging.value).toBe(false);
  });
});