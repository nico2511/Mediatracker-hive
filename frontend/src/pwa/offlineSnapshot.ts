const HOME_KEY = "mt.offline.dashboard.v1";
const MEDIA_KEY = "mt.offline.media.v1";

export interface SnapshotEnvelope<T> {
  saved_at: string;
  body: T;
}

function read<T>(key: string): SnapshotEnvelope<T> | null {
  try {
    const raw = localStorage.getItem(key);
    if (!raw) {
      return null;
    }
    const parsed = JSON.parse(raw) as SnapshotEnvelope<T>;
    if (!parsed || typeof parsed.saved_at !== "string" || parsed.body === undefined) {
      return null;
    }
    return parsed;
  } catch {
    return null;
  }
}

function write<T>(key: string, body: T): void {
  try {
    const envelope: SnapshotEnvelope<T> = { saved_at: new Date().toISOString(), body };
    localStorage.setItem(key, JSON.stringify(envelope));
  } catch {
    /* quota / mode privé */
  }
}

export function saveDashboardSnapshot(body: unknown): void {
  write(HOME_KEY, body);
}

export function loadDashboardSnapshot<T>(): SnapshotEnvelope<T> | null {
  return read<T>(HOME_KEY);
}

export function saveMediaListSnapshot(body: unknown): void {
  write(MEDIA_KEY, body);
}

export function loadMediaListSnapshot<T>(): SnapshotEnvelope<T> | null {
  return read<T>(MEDIA_KEY);
}
