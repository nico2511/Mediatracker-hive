import { formatParis, formatParisClock, formatParisDayHeading, parisDayKey } from "./time";

import type { HomeDashboardCard } from "./homeDashboardTypes";

export function progressPercent(watched: number, total: number): number {
  if (!total) {
    return 0;
  }
  return Math.round((watched / total) * 100);
}

export function episodeSlot(card: { season: number | null; episode: number | null; title?: string | null }): string {
  if (card.season == null || card.episode == null) {
    return card.title || "Film";
  }
  const code = `S${String(card.season).padStart(2, "0")}E${String(card.episode).padStart(2, "0")}`;
  return card.title ? `${code} · ${card.title}` : code;
}

export function arrivalLabel(value: string, todayKey: string, yesterdayKey: string): string {
  const key = parisDayKey(value);
  const heading = formatParisDayHeading(key, todayKey, yesterdayKey);
  const clock = formatParisClock(value);
  if (heading === "Aujourd'hui") {
    return `auj. ${clock}`;
  }
  if (heading === "Hier") {
    return `hier ${clock}`;
  }
  return formatParis(value);
}

export function recentDownloadMeta(
  card: {
    season: number | null;
    episode: number | null;
    title: string | null;
    episode_count?: number;
    downloaded_at: string;
  },
  todayKey: string,
  yesterdayKey: string,
): string {
  const when = arrivalLabel(card.downloaded_at, todayKey, yesterdayKey);
  const count = card.episode_count ?? 1;
  if (count > 1) {
    return `${count} épisodes · ${when}`;
  }
  if (card.season == null || card.episode == null) {
    return when;
  }
  const code = `S${String(card.season).padStart(2, "0")}E${String(card.episode).padStart(2, "0")}`;
  const episode = card.title ? `${code} · ${card.title}` : code;
  return `${episode} · ${when}`;
}

function episodeAirLabel(value: string | null, todayKey: string, yesterdayKey: string): string {
  if (!value) {
    return "";
  }
  const key = parisDayKey(`${value}T12:00:00`);
  const heading = formatParisDayHeading(key, todayKey, yesterdayKey);
  if (heading === "Aujourd'hui" || heading === "Hier") {
    return heading;
  }
  const parsed = new Date(`${value}T12:00:00`);
  if (Number.isNaN(parsed.getTime())) {
    return value;
  }
  return parsed.toLocaleDateString("fr-FR", { weekday: "short", day: "numeric", month: "short" });
}

export function upcomingEpisodeMeta(card: HomeDashboardCard, todayKey: string, yesterdayKey: string): string {
  const parts: string[] = [];
  if (card.air_date) {
    parts.push(episodeAirLabel(card.air_date, todayKey, yesterdayKey));
  }
  parts.push(episodeSlot(card));
  return parts.join(" · ");
}
