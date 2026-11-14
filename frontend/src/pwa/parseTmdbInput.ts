export type FollowMediaType = "film" | "series";

export interface ParsedTmdbInput {
  tmdbId: number;
  type: FollowMediaType | null;
}

const MOVIE_PATH = /\/movie\/(\d+)/i;
const TV_PATH = /\/tv\/(\d+)/i;

/** Chiffres seuls, ou URL TMDB (ID + type si présents dans le chemin). */
export function parseTmdbInput(raw: string): ParsedTmdbInput | null {
  const text = raw.trim();
  if (!text) {
    return null;
  }
  if (/^\d+$/.test(text)) {
    const tmdbId = Number(text);
    return tmdbId >= 1 ? { tmdbId, type: null } : null;
  }
  const movie = text.match(MOVIE_PATH);
  if (movie) {
    return { tmdbId: Number(movie[1]), type: "film" };
  }
  const tv = text.match(TV_PATH);
  if (tv) {
    return { tmdbId: Number(tv[1]), type: "series" };
  }
  return null;
}

export function alternateFollowType(type: FollowMediaType): FollowMediaType {
  return type === "film" ? "series" : "film";
}
