import { mount } from "@vue/test-utils";
import { describe, expect, it } from "vitest";

import FicheStateActions from "./FicheStateActions.vue";

const basePoll = {
  allowed: true,
  reason: "film_missing",
  last_check_at: null,
  next_check_at: null,
  last_result: null,
  last_message: null,
  interval_min: 720,
};

function mountActions(overrides: Record<string, unknown> = {}) {
  return mount(FicheStateActions, {
    props: {
      mediaType: "film",
      followed: true,
      onHold: false,
      autoDownload: false,
      poll: basePoll,
      progressWatched: 0,
      progressTotal: 1,
      availableNotDownloaded: 0,
      nextEpisode: null,
      fileStatus: "absent",
      filmFileAbsent: false,
      filmObtained: false,
      filmNotYetAvailable: false,
      filmStatusLine: null,
      checking: false,
      section: "actions",
      ...overrides,
    },
    global: {
      stubs: { StatusIcon: true, ActionMenuPopover: true },
    },
  });
}

describe("FicheStateActions primaryAction", () => {
  it("film absent jamais obtenu : Rechercher maintenant", () => {
    const w = mountActions({ filmFileAbsent: false });
    const primary = w.find(".fiche-primary-action");
    expect(primary.exists()).toBe(true);
    expect(primary.text()).toContain("Rechercher maintenant");
  });

  it("film absent après perte : Retélécharger", () => {
    const w = mountActions({ filmFileAbsent: true });
    const primary = w.find(".fiche-primary-action");
    expect(primary.text()).toContain("Retélécharger");
  });

  it("Rechercher maintenant émet check", async () => {
    const w = mountActions({ filmFileAbsent: false });
    await w.find(".fiche-primary-action").trigger("click");
    expect(w.emitted("check")).toHaveLength(1);
  });
});

describe("FicheStateActions bande-annonce", () => {
  it("masque le bouton sans clé YouTube", () => {
    const w = mountActions({ section: "actions" });
    expect(w.find("[data-testid='fiche-trailer-open']").exists()).toBe(false);
  });

  it("affiche le bouton quand une clé est fournie", () => {
    const w = mountActions({
      section: "actions",
      tmdbTrailerYoutubeKey: "demo-key",
      tmdbTrailerKind: "official",
      tmdbTrailerLanguage: "fr",
    });
    expect(w.find("[data-testid='fiche-trailer-open']").exists()).toBe(true);
    expect(w.find("[data-testid='fiche-trailer-open']").text()).toContain("Bande-annonce");
  });
});

describe("FicheStateActions libellé fichier film", () => {
  it("absent jamais obtenu : pas de « fichiers supprimés »", () => {
    const w = mount(FicheStateActions, {
      props: {
        mediaType: "film",
        followed: true,
        onHold: false,
        autoDownload: false,
        poll: basePoll,
        progressWatched: 0,
        progressTotal: 1,
        availableNotDownloaded: 0,
        nextEpisode: null,
        fileStatus: "absent",
        filmFileAbsent: false,
        filmObtained: false,
        filmNotYetAvailable: false,
        filmStatusLine: null,
        checking: false,
        section: "etat",
      },
      global: { stubs: { StatusIcon: true, ActionMenuPopover: true } },
    });
    const fichier = w.findAll("dt").find((n) => n.text() === "Fichier");
    expect(fichier).toBeDefined();
    const dd = fichier!.element.nextElementSibling;
    expect(dd?.textContent ?? "").toContain("Absent");
    expect(dd?.textContent ?? "").not.toContain("fichiers supprimés");
  });

  it("absent après perte : mention fichiers supprimés", () => {
    const w = mount(FicheStateActions, {
      props: {
        mediaType: "film",
        followed: true,
        onHold: false,
        autoDownload: false,
        poll: basePoll,
        progressWatched: 0,
        progressTotal: 1,
        availableNotDownloaded: 0,
        nextEpisode: null,
        fileStatus: "absent",
        filmFileAbsent: true,
        filmObtained: false,
        filmNotYetAvailable: false,
        filmStatusLine: null,
        checking: false,
        section: "etat",
      },
      global: { stubs: { StatusIcon: true, ActionMenuPopover: true } },
    });
    expect(w.text()).toContain("fichiers supprimés");
  });
});
