export const TMDB_DISPLAY_LANGUAGES = [
  { code: "fr-FR", label: "Français" },
  { code: "en-US", label: "Anglais (États-Unis)" },
  { code: "en-GB", label: "Anglais (Royaume-Uni)" },
  { code: "de-DE", label: "Allemand" },
  { code: "es-ES", label: "Espagnol" },
  { code: "it-IT", label: "Italien" },
  { code: "ja-JP", label: "Japonais" },
  { code: "ko-KR", label: "Coréen" },
] as const;

export const TMDB_REGIONS = [
  { code: "FR", label: "France" },
  { code: "BE", label: "Belgique" },
  { code: "CH", label: "Suisse" },
  { code: "CA", label: "Canada" },
  { code: "US", label: "États-Unis" },
  { code: "GB", label: "Royaume-Uni" },
  { code: "DE", label: "Allemagne" },
  { code: "ES", label: "Espagne" },
  { code: "IT", label: "Italie" },
  { code: "JP", label: "Japon" },
] as const;

export const TMDB_ORIGINAL_LANGUAGE_CHIPS = [
  { code: "fr", label: "Français" },
  { code: "en", label: "Anglais" },
  { code: "ja", label: "Japonais" },
  { code: "ko", label: "Coréen" },
  { code: "es", label: "Espagnol" },
  { code: "it", label: "Italien" },
  { code: "de", label: "Allemand" },
] as const;

export function tmdbRegionLabel(code: string): string {
  return TMDB_REGIONS.find((row) => row.code === code)?.label ?? code;
}

export function tmdbDisplayLanguageLabel(code: string): string {
  const row = TMDB_DISPLAY_LANGUAGES.find((item) => item.code === code);
  return row ? `${row.label} (${row.code})` : code;
}

export function tmdbCatalogHomeHint(region: string, displayLanguage: string): string {
  return `Rangées alimentées par TMDB (${tmdbRegionLabel(region)}, ${displayLanguage}).`;
}
