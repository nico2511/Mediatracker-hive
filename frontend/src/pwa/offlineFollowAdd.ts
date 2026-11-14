import { addQueueItem, type AddQueueResult, type QueueItem } from "./offlineQueue";
import { parseTmdbInput, type FollowMediaType } from "./parseTmdbInput";

export type SubmitOfflineFollowResult =
  | { ok: true; item: QueueItem }
  | { ok: false; reason: "invalid_input" }
  | AddQueueResult;

export async function submitOfflineFollowAdd(
  raw: string,
  fallbackKind: FollowMediaType,
): Promise<SubmitOfflineFollowResult> {
  const parsed = parseTmdbInput(raw);
  if (!parsed) {
    return { ok: false, reason: "invalid_input" };
  }
  const type = parsed.type ?? fallbackKind;
  return addQueueItem(parsed.tmdbId, type);
}
