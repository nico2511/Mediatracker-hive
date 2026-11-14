import { mount } from "@vue/test-utils";
import { describe, expect, it, vi } from "vitest";

import TmdbIdChangeDialog from "./TmdbIdChangeDialog.vue";
import type { TmdbIdChangePreview } from "../tmdbIdChange";

const preview: TmdbIdChangePreview = {
  previous_tmdb_id: 100,
  new_tmdb_id: 200,
  orphans_if_keep: [],
  removed_if_rebuild: [{ item_id: 1, season: 2, episode: 9, title: "titre fictif" }],
  files_to_reassociate: [
    {
      item_id: 2,
      season: 1,
      episode: 1,
      title: "titre fictif",
      file_paths: ["/data/demo/s01e01.mkv"],
    },
  ],
};

describe("TmdbIdChangeDialog", () => {
  it("émet cancel sur Annuler", async () => {
    const wrapper = mount(TmdbIdChangeDialog, {
      props: { open: true, preview },
      global: { stubs: { Teleport: true } },
      attachTo: document.body,
    });
    await wrapper.findAll("button").find((b) => b.text() === "Annuler")?.trigger("click");
    expect(wrapper.emitted("cancel")).toHaveLength(1);
    wrapper.unmount();
  });

  it("émet confirm rebuild sur action primaire", async () => {
    const wrapper = mount(TmdbIdChangeDialog, {
      props: { open: true, preview },
      global: { stubs: { Teleport: true } },
      attachTo: document.body,
    });
    await wrapper.get("button.primary").trigger("click");
    expect(wrapper.emitted("confirm")).toEqual([["rebuild_episodes"]]);
    wrapper.unmount();
  });

  it("n'affiche pas le dialogue si fermé", () => {
    const wrapper = mount(TmdbIdChangeDialog, {
      props: { open: false, preview },
      global: { stubs: { Teleport: true } },
    });
    expect(wrapper.find(".dialog-backdrop").exists()).toBe(false);
    wrapper.unmount();
  });
});
