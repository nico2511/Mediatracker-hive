const MAX_MEDIA_PAGES = 500;

export class MediaListCursorError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "MediaListCursorError";
  }
}

/** Valide la progression du curseur après une page API médias. */
export function assertMediaPageCursor(
  previousCursor: string | null,
  nextCursor: string | null,
  pageItems: number,
  pageIndex: number,
): void {
  if (pageIndex >= MAX_MEDIA_PAGES) {
    throw new MediaListCursorError("Pagination médias interrompue : trop de pages.");
  }
  if (pageItems === 0 && nextCursor) {
    throw new MediaListCursorError("Pagination médias incohérente : page vide avec curseur.");
  }
  if (!nextCursor) {
    return;
  }
  const prevNum = previousCursor === null ? 0 : Number(previousCursor);
  const nextNum = Number(nextCursor);
  if (!Number.isFinite(nextNum) || (previousCursor !== null && nextNum <= prevNum)) {
    throw new MediaListCursorError("Pagination médias bloquée : curseur sans progression.");
  }
  if (previousCursor !== null && nextCursor === previousCursor) {
    throw new MediaListCursorError("Pagination médias bloquée : curseur répété.");
  }
}

export { MAX_MEDIA_PAGES };
