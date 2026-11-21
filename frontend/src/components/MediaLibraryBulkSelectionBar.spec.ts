import { mount } from "@vue/test-utils";
import { describe, expect, it } from "vitest";

import MediaLibraryBulkSelectionBar from "./MediaLibraryBulkSelectionBar.vue";

describe("MediaLibraryBulkSelectionBar", () => {
  it("affiche Partager sur Hive sur ordinateur lorsque Hive est activé", () => {
    const wrapper = mount(MediaLibraryBulkSelectionBar, {
      props: {
        variant: "top",
        selectedCount: 2,
        visibleCount: 5,
        hiveEnabled: true,
        hiveShareBusy: false,
        deleteBusy: false,
        serverActionsDisabled: false,
      },
    });
    expect(wrapper.text()).toContain("Partager sur Hive");
    expect(wrapper.text()).toContain("Ne plus partager");
    expect(wrapper.text()).not.toContain("Plus");
  });

  it("masque les actions Hive lorsque Hive est désactivé", () => {
    const wrapper = mount(MediaLibraryBulkSelectionBar, {
      props: {
        variant: "top",
        selectedCount: 2,
        visibleCount: 5,
        hiveEnabled: false,
        hiveShareBusy: false,
        deleteBusy: false,
        serverActionsDisabled: false,
      },
    });
    expect(wrapper.text()).not.toContain("Partager");
    expect(wrapper.text()).not.toContain("Ne plus partager");
  });

  it("propose Partager et le menu Plus sur téléphone", () => {
    const wrapper = mount(MediaLibraryBulkSelectionBar, {
      props: {
        variant: "mobile",
        selectedCount: 4,
        visibleCount: 8,
        hiveEnabled: true,
        hiveShareBusy: false,
        deleteBusy: false,
        serverActionsDisabled: false,
      },
    });
    expect(wrapper.find(".media-library-bulk-share-label-short").exists()).toBe(true);
    expect(wrapper.text()).toContain("Plus");
    expect(wrapper.text()).toContain("4 sél.");
  });
});