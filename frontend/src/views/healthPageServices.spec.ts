import { describe, expect, it } from "vitest";

import { buildHealthServices, buildVerdict, COMPONENT_LABELS, partitionServices } from "./healthPageServices";

describe("healthPageServices", () => {
  it("utilise le libellé Bibliothèque", () => {
    expect(COMPONENT_LABELS.library).toBe("Bibliothèque");
    const services = buildHealthServices({
      components: [{ name: "library", status: "ok", message: "Bibliothèque accessible." }],
      sources: [],
      hiveEnabled: false,
      hivePayload: null,
      hiveError: "",
      update: {
        detail: "À jour.",
        updateAvailable: false,
        watchtowerConfigured: true,
        checkedAtLabel: "à 8 h 00",
      },
    });
    expect(services.some((row) => row.label === "Bibliothèque")).toBe(true);
  });

  it("formule un verdict sans problème", () => {
    const services = buildHealthServices({
      components: [
        { name: "aria2", status: "ok", message: "aria2 répond." },
        { name: "library", status: "ok", message: "Bibliothèque accessible." },
        { name: "disk", status: "ok", message: "10 Gio libres." },
        { name: "tmdb", status: "skipped", message: "Jeton TMDB absent." },
      ],
      sources: [],
      hiveEnabled: false,
      hivePayload: null,
      hiveError: "",
      update: {
        detail: "À jour.",
        updateAvailable: false,
        watchtowerConfigured: true,
        checkedAtLabel: "",
      },
    });
    const verdict = buildVerdict(services);
    expect(verdict.title).toBe("Tout va bien");
    expect(verdict.rail).toBe("ok");
  });

  it("compte panne et alerte", () => {
    const services = buildHealthServices({
      components: [
        { name: "aria2", status: "down", message: "aria2 injoignable." },
        { name: "jellyfin", status: "degraded", message: "Jeton dans l'adresse." },
        { name: "disk", status: "ok", message: "Disque OK." },
      ],
      sources: [],
      hiveEnabled: false,
      hivePayload: null,
      hiveError: "",
      update: {
        detail: "À jour.",
        updateAvailable: false,
        watchtowerConfigured: true,
        checkedAtLabel: "",
      },
    });
    const verdict = buildVerdict(services);
    expect(verdict.title).toBe("1 panne, 1 alerte");
    expect(verdict.rail).toBe("down");
  });

  it("liste Wawacity et Notifications, non configurés en bas", () => {
    const services = buildHealthServices({
      components: [
        { name: "aria2", status: "ok", message: "aria2 répond." },
        { name: "wawacity", status: "skipped", message: "Domaine absent." },
        { name: "notifier", status: "ok", message: "1 cible." },
      ],
      sources: [],
      hiveEnabled: false,
      hivePayload: null,
      hiveError: "",
      update: null,
    });
    const labels = services.map((row) => row.label);
    expect(labels).toContain("Wawacity");
    expect(labels).toContain("Notifications");
    const { skipped } = partitionServices(services);
    expect(skipped.map((row) => row.label)).toEqual(["Wawacity"]);
  });
});
