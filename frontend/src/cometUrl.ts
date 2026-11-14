/** Align Comet URL normalization with the backend (host lowercased, no trailing slash). */
export function normalizeCometInstanceUrl(text: string): string | null {
  const raw = text.trim();
  if (!raw.startsWith("http://") && !raw.startsWith("https://")) {
    return null;
  }
  try {
    const url = new URL(raw);
    url.hostname = url.hostname.toLowerCase();
    url.pathname = url.pathname.replace(/\/$/, "");
    url.hash = "";
    url.search = "";
    return url.toString().replace(/\/$/, "");
  } catch {
    return null;
  }
}

export function cometInstanceUrlKey(text: string): string {
  return normalizeCometInstanceUrl(text) ?? rawFallbackKey(text);
}

function rawFallbackKey(text: string): string {
  return text.trim().toLowerCase().replace(/\/$/, "");
}
