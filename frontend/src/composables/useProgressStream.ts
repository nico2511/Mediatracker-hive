import { onUnmounted, watch, type Ref } from "vue";

export interface ProgressJob {
  id: number;
  media_id: number;
  media_title: string;
  season: number | null;
  episode: number | null;
  hoster: string | null;
  source: string | null;
  state: string;
  step: string | null;
  progress: number;
  done_bytes: number;
  size: number | null;
  speed: number | null;
  eta_seconds: number | null;
  error_code: string | null;
  error: string | null;
  check: string | null;
  filename: string | null;
  release: string | null;
  attempts: number;
}

export interface ProgressPayload {
  queue_paused: boolean;
  queue_held?: boolean;
  queue_code?: string | null;
  queue_message: string | null;
  items?: ProgressJob[];
  downloads?: ProgressJob[];
  fiches?: Record<string, { media: Record<string, unknown>; seasons: unknown[] }>;
}

type Listener = (payload: ProgressPayload) => void;

interface Registration {
  listener: Listener;
  mediaId: number | null;
}

let source: EventSource | null = null;
let currentUrl: string | null = null;
const registrations: Registration[] = [];

function activeMediaId(): number | null {
  for (const row of registrations) {
    if (row.mediaId != null) {
      return row.mediaId;
    }
  }
  return null;
}

function streamUrl(mediaId: number | null): string {
  const base = "/api/v1/events/stream";
  return mediaId == null ? base : `${base}?media_id=${mediaId}`;
}

function ensureSource() {
  const mediaId = activeMediaId();
  const url = streamUrl(mediaId);
  if (source && currentUrl === url) {
    return;
  }
  source?.close();
  currentUrl = url;
  source = new EventSource(url, { withCredentials: true });
  source.addEventListener("progress", (event) => {
    try {
      const payload = JSON.parse((event as MessageEvent).data) as ProgressPayload;
      for (const row of registrations) {
        row.listener(payload);
      }
    } catch {
      /* ignore malformed frames */
    }
  });
  source.onerror = () => {
    source?.close();
    source = null;
    if (registrations.length) {
      window.setTimeout(ensureSource, 2000);
    }
  };
}

function teardownIfEmpty() {
  if (!registrations.length) {
    source?.close();
    source = null;
    currentUrl = null;
  }
}

export function useProgressStream(
  onPayload: Listener,
  options: { mediaId?: number | null | Ref<number | null> } = {},
): void {
  const resolveMediaId = (): number | null => {
    const value = options.mediaId;
    if (value == null) {
      return null;
    }
    if (typeof value === "object" && "value" in value) {
      const raw = value.value;
      return raw == null || raw === "" ? null : Number(raw);
    }
    return value;
  };

  const row: Registration = { listener: onPayload, mediaId: resolveMediaId() };
  registrations.push(row);
  ensureSource();

  onUnmounted(() => {
    const index = registrations.indexOf(row);
    if (index >= 0) {
      registrations.splice(index, 1);
    }
    teardownIfEmpty();
    if (registrations.length) {
      ensureSource();
    }
  });

  if (options.mediaId && typeof options.mediaId === "object" && "value" in options.mediaId) {
    watch(options.mediaId, () => {
      row.mediaId = resolveMediaId();
      ensureSource();
    });
  }
}
