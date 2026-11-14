export interface HomeDashboardCard {
  media_id: number;
  media_title: string;
  media_type?: string;
  season: number | null;
  episode: number | null;
  title: string | null;
  air_date: string | null;
  downloaded: boolean;
  watched: boolean;
  poster_url?: string | null;
  still_url?: string | null;
  year?: number | null;
  overview?: string | null;
  tmdb_id?: number | null;
}

export interface HomeDashboardDownloadCard {
  id: number;
  state: string;
  progress: number;
  speed: number | null;
  eta_seconds: number | null;
  error_code: string | null;
  error: string | null;
  media_title: string | null;
  season: number | null;
  episode: number | null;
}

export interface HomeProgressCard {
  media_id: number;
  media_title: string;
  watched: number;
  total: number;
  poster_url?: string | null;
  last_watched_at?: string | null;
  media_type?: string;
  year?: number | null;
  overview?: string | null;
  tmdb_id?: number | null;
}

export interface HomeWatchGroup {
  media_id: number;
  media_title: string;
  media_type: string;
  count: number;
  label: string;
  poster_url?: string | null;
  year?: number | null;
  overview?: string | null;
  tmdb_id?: number | null;
  items: HomeDashboardCard[];
}

export interface HomeRecentDownloadCard {
  media_id: number;
  media_title: string;
  media_type: string;
  season: number | null;
  episode: number | null;
  title: string | null;
  episode_count?: number;
  poster_url?: string | null;
  downloaded_at: string;
  year?: number | null;
  overview?: string | null;
  tmdb_id?: number | null;
}

export type HomePersonalSectionKey = "progress" | "upcomingWeek" | "recentDownloads" | "toWatch";

export interface HomePersonalDashboardSlice {
  to_watch_groups: HomeWatchGroup[];
  upcoming_week: HomeDashboardCard[];
  recent_downloads: HomeRecentDownloadCard[];
  progress: HomeProgressCard[];
  downloads_active: HomeDashboardDownloadCard[];
  downloads_failed: HomeDashboardDownloadCard[];
}
