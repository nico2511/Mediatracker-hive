export function mediaKind(type: string | null | undefined): string {
  if (type === "film") {
    return "Film";
  }
  if (type === "series") {
    return "Série";
  }
  return type || "";
}

export function tmdbPage(id: number, type: string): string {
  const section = type === "film" ? "movie" : "tv";
  return `https://www.themoviedb.org/${section}/${id}`;
}

export function tmdbSearch(query: string): string {
  return `https://www.themoviedb.org/search?query=${encodeURIComponent(query.trim())}`;
}

export function candidateFacts(hit: {
  year?: number | null;
  media_type?: string;
  season_count?: number | null;
  episode_count?: number | null;
  runtime?: number | null;
}): string {
  const parts: string[] = [];
  if (hit.year) {
    parts.push(String(hit.year));
  }
  if (hit.media_type) {
    parts.push(mediaKind(hit.media_type));
  }
  if (hit.season_count) {
    parts.push(`${hit.season_count} saison${hit.season_count > 1 ? "s" : ""}`);
  }
  if (hit.episode_count) {
    parts.push(`${hit.episode_count} épisode${hit.episode_count > 1 ? "s" : ""}`);
  }
  if (hit.runtime) {
    parts.push(`${hit.runtime} min`);
  }
  return parts.join(" · ");
}
