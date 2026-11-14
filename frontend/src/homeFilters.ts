import type { HomeFilterKey } from "./components/HomeFilterChips.vue";

export type HomeSectionKey =
  | "toWatch"
  | "recentDownloads"
  | "available"
  | "upcomingWeek"
  | "upcomingReleases"
  | "catalogFilms"
  | "catalogSeries"
  | "hiveDispos"
  | "progress";

const SECTION_FILTERS: Record<HomeSectionKey, HomeFilterKey[]> = {
  toWatch: ["films", "series"],
  recentDownloads: ["films", "series"],
  available: ["films", "series"],
  upcomingWeek: ["series", "upcoming"],
  upcomingReleases: ["films", "series", "upcoming"],
  catalogFilms: ["films"],
  catalogSeries: ["series"],
  hiveDispos: ["films", "series"],
  progress: ["films", "series"],
};

export function sectionVisible(key: HomeSectionKey, active: HomeFilterKey[]): boolean {
  if (!active.length) {
    return true;
  }
  const allowed = SECTION_FILTERS[key];
  return allowed.some((filter) => active.includes(filter));
}

export function matchesMediaType(mediaType: string | undefined, active: HomeFilterKey[]): boolean {
  if (!active.length) {
    return true;
  }
  const wantsFilms = active.includes("films");
  const wantsSeries = active.includes("series");
  if (!wantsFilms && !wantsSeries) {
    return true;
  }
  const kind = mediaType === "film" ? "film" : "series";
  if (kind === "film") {
    return wantsFilms;
  }
  return wantsSeries;
}

export const FILTER_STORAGE_KEY = "mediatracker.home.filters.v1";

export function loadStoredFilters(): HomeFilterKey[] {
  try {
    const raw = localStorage.getItem(FILTER_STORAGE_KEY);
    if (!raw) {
      return [];
    }
    const parsed = JSON.parse(raw) as unknown;
    if (!Array.isArray(parsed)) {
      return [];
    }
    const allowed = new Set<HomeFilterKey>(["films", "series", "upcoming"]);
    return parsed.filter((entry): entry is HomeFilterKey => typeof entry === "string" && allowed.has(entry as HomeFilterKey));
  } catch {
    return [];
  }
}

export function storeFilters(active: HomeFilterKey[]): void {
  try {
    localStorage.setItem(FILTER_STORAGE_KEY, JSON.stringify(active));
  } catch {
    // ignore
  }
}

export const SECTION_ANCHORS: Record<HomeSectionKey, string> = {
  toWatch: "section-a-voir",
  recentDownloads: "section-derniers-telechargements",
  available: "section-a-telecharger",
  upcomingWeek: "section-prochains-episodes",
  upcomingReleases: "section-a-venir",
  catalogFilms: "section-derniers-films",
  catalogSeries: "section-dernieres-series",
  hiveDispos: "section-hive-dispos",
  progress: "section-progression",
};
