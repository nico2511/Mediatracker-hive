import { mount } from "@vue/test-utils";
import { describe, expect, it } from "vitest";

import DocLink from "./DocLink.vue";

describe("DocLink", () => {
  it("expose target et rel pour les liens externes", () => {
    const wrapper = mount(DocLink, {
      props: { href: "https://example.org/doc", label: "Documentation" },
    });
    const link = wrapper.get("a");
    expect(link.attributes("href")).toBe("https://example.org/doc");
    expect(link.attributes("target")).toBe("_blank");
    expect(link.attributes("rel")).toBe("noreferrer noopener");
    expect(link.classes()).toContain("doc-link");
    expect(link.text()).toBe("Documentation ↗");
  });
});