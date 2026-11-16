export interface DiscoverCard {
  tmdb_id: number;
  media_type: string;
  title: string;
  year?: number | null;
  release_date?: string | null;
  overview?: string | null;
  poster_url?: string | null;
  tmdb_url?: string | null;
  followed?: boolean;
}

export type DiscoverMediaFilter = "all" | "film" | "series";

export type DiscoverGesture = "follow" | "pass" | "sheet" | "later";