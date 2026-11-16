const TMDB_PATH = /^https:\/\/image\.tmdb\.org\/t\/p\/[^/]+\/(.+)$/;

function fileFromTmdb(url: string | null | undefined): string | null {
  if (!url) return null;
  const match = TMDB_PATH.exec(url.trim());
  if (!match) return null;
  const file = match[1];
  if (!file || file.includes("..") || file.includes("/")) return null;
  return file;
}

export function tmdbPosterSrc(url: string | null | undefined, size = "w342"): string | null {
  const file = fileFromTmdb(url);
  if (!file) return url ?? null;
  return `/api/v1/images/poster/${size}/${encodeURIComponent(file)}`;
}

export function tmdbBackdropSrc(url: string | null | undefined, size = "w780"): string | null {
  const file = fileFromTmdb(url);
  if (!file) return url ?? null;
  return `/api/v1/images/backdrop/${size}/${encodeURIComponent(file)}`;
}

export function tmdbPosterSrcSet(url: string | null | undefined, thumb = false): string | undefined {
  const file = fileFromTmdb(url);
  if (!file) return undefined;
  const enc = encodeURIComponent(file);
  if (thumb) {
    return [
      `/api/v1/images/poster/w92/${enc} 92w`,
      `/api/v1/images/poster/w185/${enc} 185w`,
    ].join(", ");
  }
  return [
    `/api/v1/images/poster/w185/${enc} 185w`,
    `/api/v1/images/poster/w342/${enc} 342w`,
    `/api/v1/images/poster/w780/${enc} 780w`,
  ].join(", ");
}

export function tmdbPosterDims(size: "w92" | "w185" | "w342" | "w780"): { width: number; height: number } {
  const width = size === "w92" ? 92 : size === "w185" ? 185 : size === "w780" ? 780 : 342;
  return { width, height: Math.round(width * 1.5) };
}
