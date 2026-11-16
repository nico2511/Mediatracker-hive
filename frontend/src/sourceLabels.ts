/** Libellés affichés pour les identifiants de sources de téléchargement. */
export const SOURCE_DISPLAY_LABELS: Record<string, string> = {
  wawacity: "Wawacity",
  free_telecharger: "Free-Télécharger",
  hive: "Hive",
  comet: "Comet",
  torrentio: "Torrentio",
  stremio_addons: "Addons Stremio",
};

export function sourceDisplayLabel(id: string, fromApi?: string | null): string {
  const api = (fromApi || "").trim();
  if (api && api !== id) {
    return api;
  }
  return SOURCE_DISPLAY_LABELS[id] ?? (api || id);
}