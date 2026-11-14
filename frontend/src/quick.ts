export interface QuickTarget {
  title: string;
  year?: number | null;
  overview?: string | null;
  posterUrl?: string | null;
  mediaType: string;
  followed?: boolean;
  tmdbUrl?: string | null;
  tmdbId?: number | null;
  mediaId?: number | null;
  catalogId?: number | null;
  sourceUrl?: string | null;
  jellyfinUrl?: string | null;
  ignored?: boolean;
  season?: number | null;
}
