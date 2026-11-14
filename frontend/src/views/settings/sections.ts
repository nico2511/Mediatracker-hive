export interface SettingsSectionDef {
  slug: string;
  title: string;
}

export const SETTINGS_SECTIONS: SettingsSectionDef[] = [
  { slug: "compte", title: "Compte" },
  { slug: "qualite", title: "Profils de qualité" },
  { slug: "sources", title: "Sources" },
  { slug: "scan", title: "Réglages de scan" },
  { slug: "jellyfin", title: "Jellyfin" },
  { slug: "notifications", title: "Notifications" },
  { slug: "nettoyage", title: "Nettoyage" },
  { slug: "alldebrid", title: "AllDebrid" },
  { slug: "disque", title: "Disque et sauvegardes" },
  { slug: "hive", title: "Hive" },
  { slug: "application", title: "Application" },
  { slug: "maj", title: "Mise à jour et redémarrage" },
  { slug: "jetons", title: "Jetons d'API" },
  { slug: "reset", title: "Réinitialiser la base" },
];

export const SETTINGS_SECTION_SLUGS = new Set(SETTINGS_SECTIONS.map((row) => row.slug));

export function sectionBySlug(slug: string): SettingsSectionDef | undefined {
  return SETTINGS_SECTIONS.find((row) => row.slug === slug);
}
