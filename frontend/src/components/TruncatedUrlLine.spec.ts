import { mount } from "@vue/test-utils";
import { describe, expect, it } from "vitest";

import TruncatedUrlLine from "./TruncatedUrlLine.vue";

describe("TruncatedUrlLine", () => {
  it("tronque l’URL sur une ligne et propose Voir / Copier", async () => {
    const longUrl =
      "https://addon.fictif.example/eyJkaXJlY3RfbW9kZSI6ImRpcmVjdF9vbmx5IiwiYWN0aXZlX3ByZXNldCI6ImNvbXBsZXRlIn0";
    const wrapper = mount(TruncatedUrlLine, { props: { url: longUrl } });
    const code = wrapper.find("code");
    expect(code.attributes("title")).toBe(longUrl);
    expect(code.classes()).not.toContain("url-ellipsis-line--open");
    expect(wrapper.text()).toContain("Voir");
    expect(wrapper.text()).toContain("Copier");
    await wrapper.findAll("button").find((btn) => btn.text() === "Voir")?.trigger("click");
    expect(wrapper.find(".url-ellipsis-line").classes()).toContain("url-ellipsis-line--open");
    expect(wrapper.text()).toContain("Réduire");
  });
});