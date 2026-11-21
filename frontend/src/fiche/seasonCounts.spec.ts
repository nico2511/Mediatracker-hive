import { describe, expect, it, vi } from "vitest";

import {
  absentWithoutSourceCount,
  browserLocalTodayIso,
  catalogEpisodeCount,
  missingCatalogSlots,
  onDiskTone,
  presentOnDiskCount,
  readyWithoutFileCount,
  readyWithoutFileLabel,
  watchedTone,
  type SeasonCountsGroup,
} from "./seasonCounts";

const REF = "2026-11-21";

function season(overrides: Partial<SeasonCountsGroup>): SeasonCountsGroup {
  return {
    episode_count: null,
    episodes: [],
    ...overrides,
  };
}

describe("seasonCounts", () => {
  it("sépare fichier sur disque, source dispo et épisode hors suivi", () => {
    const group = season({
      episode_count: 7,
      file_status_counts: { present: 6, trash: 0, absent: 1, downloading: 0 },
      episodes: Array.from({ length: 6 }, (_, index) => ({
        watched: false,
        downloaded: index < 6,
        file_status: index < 6 ? "present" : "absent",
        source_available: false,
      })).concat({
        watched: false,
        downloaded: false,
        file_status: "absent",
        source_available: true,
        download_active: false,
      }),
    });
    expect(presentOnDiskCount(group)).toBe(6);
    expect(catalogEpisodeCount(group)).toBe(7);
    expect(missingCatalogSlots(group)).toBe(0);
    expect(readyWithoutFileCount(group)).toBe(1);
    expect(readyWithoutFileLabel(group)).toBe("1 à télécharger");
    expect(absentWithoutSourceCount(group)).toBe(0);
  });

  it("compte les épisodes TMDB sans ligne MediaItem", () => {
    const group = season({
      episode_count: 7,
      file_status_counts: { present: 6, trash: 0, absent: 0, downloading: 0 },
      episodes: Array.from({ length: 6 }, () => ({
        watched: false,
        downloaded: true,
        file_status: "present",
        source_available: false,
      })),
    });
    expect(missingCatalogSlots(group)).toBe(1);
    expect(readyWithoutFileCount(group)).toBe(0);
  });

  it("n’emploie pas à la fois à télécharger et sans fichier ni source pendant un téléchargement", () => {
    const group = season({
      file_status_counts: { present: 0, trash: 0, absent: 0, downloading: 1 },
      episodes: [
        {
          watched: false,
          downloaded: false,
          file_status: "downloading",
          source_available: true,
          download_active: true,
          download_busy: true,
        },
      ],
    });
    expect(readyWithoutFileCount(group)).toBe(0);
    expect(absentWithoutSourceCount(group)).toBe(0);
  });

  it("vert vus et disque quand les épisodes diffusés sont complets", () => {
    const group = season({
      episodes: [
        { watched: true, downloaded: true, file_status: "present", source_available: false, air_date: "2022-01-01" },
        { watched: true, downloaded: true, file_status: "present", source_available: false, air_date: "2022-01-08" },
        {
          watched: false,
          downloaded: false,
          file_status: "absent",
          source_available: false,
          air_date: "2099-12-01",
        },
      ],
    });
    expect(watchedTone(group, REF)).toBe("ok");
    expect(onDiskTone(group, REF)).toBe("ok");
  });

  it("ambre si partiel parmi les diffusés", () => {
    const group = season({
      episodes: [
        { watched: true, downloaded: true, file_status: "present", source_available: false, air_date: "2022-03-01" },
        { watched: false, downloaded: true, file_status: "present", source_available: false, air_date: "2022-03-08" },
        { watched: false, downloaded: false, file_status: "absent", source_available: false, air_date: "2022-03-15" },
      ],
    });
    expect(watchedTone(group, REF)).toBe("warn");
    expect(onDiskTone(group, REF)).toBe("warn");
  });

  it("sans fichier ni source seulement pour les épisodes déjà diffusés", () => {
    const group = season({
      episodes: [
        { watched: false, downloaded: false, file_status: "absent", source_available: false, air_date: "2026-11-08" },
        { watched: false, downloaded: false, file_status: "absent", source_available: false, air_date: "2099-12-01" },
      ],
    });
    expect(absentWithoutSourceCount(group, REF)).toBe(1);
  });

  it("utilise la date locale injectée sans passer par toISOString", () => {
    const spy = vi.spyOn(Date.prototype, "toISOString");
    const clock = new Date(2026, 10, 21, 23, 30, 0);
    expect(browserLocalTodayIso(clock)).toBe("2026-11-21");
    expect(spy).not.toHaveBeenCalled();
    spy.mockRestore();
  });
});
