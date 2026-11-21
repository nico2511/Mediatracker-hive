import { tmdbRegionLabel } from "../views/settings/tmdbCatalogMeta";

export type ReleaseKind = "theatrical" | "digital" | "physical";

export type ReleaseByRegion = Record<string, Partial<Record<ReleaseKind, string | null>>>;

const ROW_LABELS: Record<ReleaseKind, string> = {
  theatrical: "En salles",
  digital: "Numérique",
  physical: "Physique",
};

export function filmReleaseRowLabel(kind: ReleaseKind): string {
  return ROW_LABELS[kind];
}

export function formatFilmReleaseShort(iso: string | null | undefined): string {
  if (!iso) {
    return "";
  }
  const parsed = new Date(`${iso.slice(0, 10)}T12:00:00`);
  if (Number.isNaN(parsed.getTime())) {
    return iso;
  }
  return parsed.toLocaleDateString("fr-FR", { day: "numeric", month: "short", year: "numeric" });
}

export function filmReleaseDisplayRegions(catalogRegion: string): string[] {
  const catalog = catalogRegion.trim().toUpperCase() || "FR";
  if (catalog === "US") {
    return ["US"];
  }
  return ["US", catalog];
}

export function highlightRegionForSource(
  source: string | null | undefined,
  catalogRegion: string,
): { kind: ReleaseKind | null; region: string | null } {
  if (!source) {
    return { kind: null, region: null };
  }
  if (source === "digital_us") {
    return { kind: "digital", region: "US" };
  }
  if (source === "digital_any") {
    return { kind: null, region: null };
  }
  if (source.startsWith("digital_")) {
    return { kind: "digital", region: catalogRegion.toUpperCase() || "FR" };
  }
  if (source === "physical_fr" || source.startsWith("physical_")) {
    return { kind: "physical", region: catalogRegion.toUpperCase() || "FR" };
  }
  if (source === "theatrical_fallback" || source === "theatrical_only") {
    return { kind: "theatrical", region: catalogRegion.toUpperCase() || "FR" };
  }
  return { kind: null, region: null };
}

export function filmSearchFooter(
  source: string | null | undefined,
  searchFrom: string | null | undefined,
  catalogRegion: string,
): string {
  if (!searchFrom || !source) {
    return "";
  }
  const when = formatFilmReleaseShort(searchFrom);
  if (!when) {
    return "";
  }
  if (source === "digital_us") {
    return `Recherche des liens à partir du ${when}, première sortie numérique (États-Unis).`;
  }
  if (source === "digital_any") {
    return `Recherche des liens à partir du ${when}, première sortie numérique ou physique connue.`;
  }
  if (source.startsWith("digital_")) {
    const label = tmdbRegionLabel(catalogRegion.toUpperCase() || "FR");
    return `Recherche des liens à partir du ${when}, première sortie numérique (${label}).`;
  }
  if (source === "physical_fr" || source.startsWith("physical_")) {
    const label = tmdbRegionLabel(catalogRegion.toUpperCase() || "FR");
    return `Recherche des liens à partir du ${when}, sortie physique (${label}).`;
  }
  if (source === "theatrical_fallback") {
    return `Recherche des liens à partir du ${when} (estimation après sortie en salle).`;
  }
  if (source === "theatrical_only") {
    const label = tmdbRegionLabel(catalogRegion.toUpperCase() || "FR");
    return `Recherche des liens à partir du ${when}, sortie en salle (${label}).`;
  }
  return "";
}

export function filmSearchFooterParts(
  source: string | null | undefined,
  searchFrom: string | null | undefined,
  catalogRegion: string,
): { prefix: string; date: string; suffix: string } | null {
  const full = filmSearchFooter(source, searchFrom, catalogRegion);
  if (!full || !searchFrom) {
    return null;
  }
  const when = formatFilmReleaseShort(searchFrom);
  if (!when) {
    return null;
  }
  const index = full.indexOf(when);
  if (index < 0) {
    return null;
  }
  return {
    prefix: full.slice(0, index),
    date: when,
    suffix: full.slice(index + when.length),
  };
}

export function regionLineLabel(code: string): string {
  return tmdbRegionLabel(code);
}