import { mount } from "@vue/test-utils";
import { describe, expect, it } from "vitest";

import TmdbRatingCardLine from "./TmdbRatingCardLine.vue";

describe("TmdbRatingCardLine", () => {
  it("affiche la note avec libellé accessible", () => {
    const wrapper = mount(TmdbRatingCardLine, { props: { voteAverage: 7.4 } });
    expect(wrapper.text()).toContain("★");
    expect(wrapper.text()).toContain("7,4");
    expect(wrapper.find(".visually-hidden").text()).toBe("Note TMDB 7,4 sur 10");
  });

  it("n’affiche rien sans note", () => {
    const wrapper = mount(TmdbRatingCardLine, { props: { voteAverage: null } });
    expect(wrapper.text()).toBe("");
  });
});