const JUNK = /[([]\s*(?:french|truefrench|vostfr|vff|vfq|multi|vf|vo|en|2160p|1080p|720p|480p|4k|uhd|hdlight|hd|sd|webrip|web-dl|webdl|bluray|bdrip)\s*[)\]]/gi;
const EMPTY = /[([]\s*[)\]]/g;

/** Same cleanup as the API: empty parentheses and release-language tags. */
export function cleanDisplayTitle(raw: string | null | undefined): string {
  const text = (raw || "").replace(/\s+/g, " ").trim();
  if (!text) {
    return "";
  }
  let cleaned = text;
  let previous = "";
  while (cleaned !== previous) {
    previous = cleaned;
    cleaned = cleaned.replace(JUNK, " ").replace(EMPTY, " ").replace(/\s+/g, " ").replace(/^[\s\-–—:|]+|[\s\-–—:|]+$/g, "");
  }
  return cleaned || text;
}
