import { mount } from "@vue/test-utils";
import { describe, expect, it } from "vitest";

import TrailerPlayerDialog from "./TrailerPlayerDialog.vue";

describe("TrailerPlayerDialog", () => {
  it("iframe youtube-nocookie avec referrerpolicy cross-origin", () => {
    const wrapper = mount(TrailerPlayerDialog, {
      props: {
        open: true,
        youtubeKey: "demo-embed-key",
        subtitle: "Officielle · français",
      },
      global: { stubs: { Teleport: true } },
      attachTo: document.body,
    });
    const frame = wrapper.find("iframe.trailer-player-frame");
    expect(frame.exists()).toBe(true);
    expect(frame.attributes("src")).toBe("https://www.youtube-nocookie.com/embed/demo-embed-key");
    expect(frame.attributes("referrerpolicy")).toBe("strict-origin-when-cross-origin");
    wrapper.unmount();
  });
});