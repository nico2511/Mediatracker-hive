export type SettingsGroupId =
  | "general"
  | "services"
  | "telechargement"
  | "mediatheque"
  | "stockage";

export const SETTINGS_GROUPS: { id: SettingsGroupId; title: string }[] = [
  { id: "general", title: "Général" },
  { id: "telechargement", title: "Téléchargement" },
  { id: "mediatheque", title: "Médiathèque" },
  { id: "stockage", title: "Stockage" },
];

export interface SettingsSectionDef {
  slug: string;
  title: string;
  group: SettingsGroupId;
}

export const SETTINGS_SECTIONS: SettingsSectionDef[] = [
  { slug: "compte", title: "Compte", group: "general" },
  { slug: "application", title: "Application", group: "general" },
  { slug: "maj", title: "Mise à jour et redémarrage", group: "general" },
  { slug: "jetons", title: "API et jetons", group: "general" },
  { slug: "sources", title: "Sources", group: "telechargement" },
  { slug: "qualite", title: "Profils de qualité", group: "telechargement" },
  { slug: "scan", title: "Réglages de scan", group: "telechargement" },
  { slug: "alldebrid", title: "AllDebrid", group: "telechargement" },
  { slug: "hive", title: "Hive", group: "telechargement" },
  { slug: "jellyfin", title: "Jellyfin", group: "mediatheque" },
  { slug: "tmdb-catalog", title: "Catalogue TMDB", group: "mediatheque" },
  { slug: "notifications", title: "Notifications", group: "mediatheque" },
  { slug: "disque", title: "Disque et sauvegardes", group: "stockage" },
  { slug: "corbeille", title: "Corbeille", group: "stockage" },
  { slug: "reset", title: "Réinitialiser la base", group: "stockage" },
];

export const SETTINGS_SECTION_SLUGS = new Set(SETTINGS_SECTIONS.map((row) => row.slug));

export function sectionBySlug(slug: string): SettingsSectionDef | undefined {
  return SETTINGS_SECTIONS.find((row) => row.slug === slug);
}

export function settingsSectionsByGroup(): {
  id: SettingsGroupId;
  title: string;
  sections: SettingsSectionDef[];
}[] {
  return SETTINGS_GROUPS.map((group) => ({
    ...group,
    sections: SETTINGS_SECTIONS.filter((row) => row.group === group.id),
  }));
}