import { flushPromises, mount } from "@vue/test-utils";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { resetDiskUsageForTests } from "../useDiskUsage";
import DiskSpaceCard from "./DiskSpaceCard.vue";

const apiMock = vi.hoisted(() => vi.fn());

vi.mock("../api", () => ({
  api: apiMock,
  userFacingApiError: (_e: unknown, f: string) => f,
}));

const baseFolders = [
  {
    key: "movies",
    label: "Films",
    path: "/data/films",
    bytes: 0,
    missing: false,
    device: "",
    mount: "/data",
  },
  {
    key: "incomplete",
    label: "Téléchargements incomplets",
    path: "/data/incomplete",
    bytes: 1000,
    written_bytes: 1000,
    reserved_bytes: 2000,
    missing: false,
    device: "",
    mount: "/data",
    files: [{ name: "demo.mkv", written_bytes: 1000, reserved_bytes: 2000, job_id: null, job_state: "", orphan: false }],
    incomplete_trash_count: 0,
    incomplete_kept_recent_count: 2,
    incomplete_keep_until: "2026-10-22T12:00:00+00:00",
  },
];

describe("DiskSpaceCard — incomplets corbeille", () => {
  beforeEach(() => {
    resetDiskUsageForTests();
    apiMock.mockReset();
    apiMock.mockResolvedValue({
      computed_at: "2026-10-20T12:00:00+00:00",
      running: false,
      error: "",
      free_bytes: 1_000_000,
      folders: baseFolders,
      volumes: [],
    });
  });

  it("masque le bouton corbeille et affiche les fichiers gardés (section réglages)", async () => {
    const wrapper = mount(DiskSpaceCard, {
      props: { panel: "incomplete" },
      global: {
        stubs: { RouterLink: { template: "<a><slot /></a>", props: ["to"] } },
      },
    });
    await flushPromises();
    expect(wrapper.find("[data-incomplete-trash-link]").exists()).toBe(false);
    const noTrash = wrapper.find("[data-incomplete-no-trash]");
    expect(noTrash.exists()).toBe(true);
    expect(noTrash.text()).toContain("Rien à mettre à la corbeille");
    expect(noTrash.text()).toMatch(/2 fichiers récents gardés/);
    expect(noTrash.text()).toMatch(/22\/10/);
  });

  it("affiche le bouton quand des orphelins sont éligibles", async () => {
    apiMock.mockResolvedValueOnce({
      computed_at: "2026-10-20T12:00:00+00:00",
      running: false,
      error: "",
      free_bytes: 1_000_000,
      folders: [
        ...baseFolders.filter((row) => row.key !== "incomplete"),
        {
          ...baseFolders[1],
          incomplete_trash_count: 1,
          incomplete_kept_recent_count: 0,
          incomplete_keep_until: null,
          files: [{ name: "old.mkv", written_bytes: 1000, reserved_bytes: 2000, job_id: null, job_state: "", orphan: true }],
        },
      ],
      volumes: [],
    });
    const wrapper = mount(DiskSpaceCard, {
      props: { panel: "incomplete" },
      global: {
        stubs: { RouterLink: { template: "<a><slot /></a>", props: ["to"] } },
      },
    });
    await flushPromises();
    expect(wrapper.find("[data-incomplete-trash-link]").exists()).toBe(true);
    expect(wrapper.find("[data-incomplete-no-trash]").exists()).toBe(false);
  });

  it("replie le détail des dossiers dans le panneau disque", async () => {
    const wrapper = mount(DiskSpaceCard, {
      props: { panel: "disk" },
      global: {
        stubs: { RouterLink: true },
      },
    });
    await flushPromises();
    const details = wrapper.find("[data-disk-folders-details]");
    expect(details.exists()).toBe(true);
    expect(details.attributes("open")).toBeUndefined();
  });
});