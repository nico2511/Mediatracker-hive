/** Sous-titre bande-annonce TMDB (ex. « Officielle · français »). */

const KIND_LABELS: Record<string, string> = {
  official: "Officielle",
  unofficial: "Non officielle",
  teaser: "Teaser",
};

const LANGUAGE_LABELS: Record<string, string> = {
  fr: "français",
  en: "anglais",
};

export function trailerSubtitle(kind: string | null | undefined, language: string | null | undefined): string | null {
  if (!kind) {
    return null;
  }
  const typeLabel = KIND_LABELS[kind];
  if (!typeLabel) {
    return null;
  }
  if (!language) {
    return typeLabel;
  }
  const langLabel = LANGUAGE_LABELS[language];
  if (!langLabel) {
    return typeLabel;
  }
  return `${typeLabel} · ${langLabel}`;
}