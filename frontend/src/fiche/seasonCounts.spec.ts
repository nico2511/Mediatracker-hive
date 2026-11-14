import { describe, expect, it } from "vitest";

import {
  absentWithoutSourceCount,
  catalogEpisodeCount,
  missingCatalogSlots,
  presentOnDiskCount,
  readyWithoutFileCount,
  readyWithoutFileLabel,
  type SeasonCountsGroup,
} from "./seasonCounts";

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
});
