import { flushPromises, mount } from "@vue/test-utils";
import { describe, expect, it, vi } from "vitest";

const apiMock = vi.hoisted(() => vi.fn());

vi.mock("../../api", () => ({
  api: (...args: unknown[]) => apiMock(...args),
  userFacingApiError: (_e: unknown, fallback: string) => fallback,
}));

vi.mock("vue-router", () => ({
  RouterLink: { template: "<a><slot /></a>", props: ["to"] },
}));

import HiveCatalogueVersionList from "./HiveCatalogueVersionList.vue";

const VERSIONS = [
  {
    version_key: "HD+VFF",
    disponible: true,
    source_count: 1,
    profile_compatible: true,
    recommended: true,
  },
  {
    version_key: "1080p+VFF",
    disponible: true,
    source_count: 1,
    profile_compatible: true,
    recommended: false,
  },
  {
    version_key: "4K+VOSTFR",
    disponible: true,
    source_count: 1,
    profile_compatible: false,
    recommended: false,
  },
];

describe("HiveCatalogueVersionList", () => {
  it("affiche le badge et la confirmation pour une version hors profil", async () => {
    apiMock.mockResolvedValue({ queued: true });
    const w = mount(HiveCatalogueVersionList, {
      props: { workKey: "wk-test", versions: VERSIONS },
    });
    expect(w.text()).toMatch(/Conseillée/);
    expect(w.text()).toContain("Compatible");
    expect(w.text()).toContain("Hors profil");
    const conseillee = w.findAll(".pill.ok");
    expect(conseillee.length).toBe(1);
    const offBtn = w.findAll("button").find((b) => b.text() === "Télécharger" && !b.classes("primary"));
    expect(offBtn).toBeTruthy();
    await offBtn!.trigger("click");
    expect(w.get("[data-testid=hive-version-confirm]").text()).toContain("Télécharger quand même");
    await w.get("[data-testid=hive-version-confirm-btn]").trigger("click");
    await flushPromises();
    expect(apiMock).toHaveBeenCalledWith(
      "/api/v1/hive/catalogue/wk-test/request",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ version_key: "4K+VOSTFR" }),
      }),
    );
    expect(w.get("[data-testid=hive-version-requested]").text()).toContain("Téléchargements");
    expect(w.get("[data-testid=hive-version-sent]").text()).toContain("Demande envoyée");
  });

  it("affiche l’erreur sous la ligne concernée", async () => {
    apiMock.mockRejectedValue(new Error("x"));
    const w = mount(HiveCatalogueVersionList, {
      props: { workKey: "wk-test", versions: [VERSIONS[0]] },
    });
    await w.get("[data-testid=hive-version-download-HD+VFF]").trigger("click");
    await flushPromises();
    expect(w.get(`[data-testid=hive-version-error-HD+VFF]`).text()).toContain("Transfert indisponible");
    expect(w.get("[data-testid=hive-version-download-HD+VFF]").text()).toBe("Télécharger");
  });
});