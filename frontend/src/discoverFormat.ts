import { mediaKind } from "./labels";
import type { DiscoverCard } from "./discoverTypes";

export function discoverDateLabel(card: Pick<DiscoverCard, "release_date" | "year">): string {
  if (card.release_date) {
    const parsed = new Date(`${card.release_date}T12:00:00`);
    if (!Number.isNaN(parsed.getTime())) {
      return parsed.toLocaleDateString("fr-FR", { day: "numeric", month: "long", year: "numeric" });
    }
  }
  if (card.year != null) {
    return String(card.year);
  }
  return "";
}

export function discoverInfoLine(card: Pick<DiscoverCard, "media_type" | "release_date" | "year">): string {
  const kind = mediaKind(card.media_type);
  const when = discoverDateLabel(card);
  return when ? `${kind} · ${when}` : kind;
}