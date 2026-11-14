export const PROFILE_FALLBACK_ORDER_KEY = "profile_fallback_order";

export interface QualityProfileLike {
  id: number;
  name: string;
  languages: string[];
  min_resolution: string | null;
  max_resolution: string | null;
  min_size_mb: number | null;
  max_size_mb: number | null;
  is_default?: boolean;
}

export function parseProfileFallbackOrder(raw: string): number[] {
  const text = (raw || "").trim();
  if (!text) {
    return [];
  }
  const found: number[] = [];
  for (const part of text.split(",")) {
    const piece = part.trim();
    if (!piece || !/^\d+$/.test(piece)) {
      continue;
    }
    const profileId = Number(piece);
    if (profileId > 0 && !found.includes(profileId)) {
      found.push(profileId);
    }
  }
  return found;
}

export function formatProfileFallbackOrder(ids: number[]): string {
  return ids.join(",");
}

/** Principal first, then global fallbacks when allowed; dedupe by id. */
export function buildEffectiveProfileChain(
  principalId: number | null,
  globalOrder: number[],
  allowFallback: boolean,
): number[] {
  if (principalId == null || principalId <= 0) {
    return [];
  }
  const chain = [principalId];
  if (!allowFallback || globalOrder.length === 0) {
    return chain;
  }
  const seen = new Set(chain);
  for (const profileId of globalOrder) {
    if (seen.has(profileId)) {
      continue;
    }
    seen.add(profileId);
    chain.push(profileId);
  }
  return chain;
}

function formatResolution(profile: QualityProfileLike): string | null {
  const min = profile.min_resolution?.trim() || "";
  const max = profile.max_resolution?.trim() || "";
  if (min && max && min !== max) {
    return `${min} à ${max}`;
  }
  if (max) {
    return max;
  }
  if (min) {
    return min;
  }
  return null;
}

function formatSizeCap(mb: number | null): string | null {
  if (mb == null || mb <= 0) {
    return null;
  }
  if (mb >= 1024) {
    const go = mb / 1024;
    const label = Number.isInteger(go) ? String(go) : go.toFixed(1).replace(/\.0$/, "");
    return `jusqu'à ${label} Go`;
  }
  return `jusqu'à ${mb} Mo`;
}

/** One-line summary for lists and selects (mockup: 720p · VF · jusqu'à 2 Go). */
export function qualityProfileSummary(profile: QualityProfileLike): string {
  const parts: string[] = [];
  const resolution = formatResolution(profile);
  if (resolution) {
    parts.push(resolution);
  }
  if (profile.languages.length) {
    parts.push(profile.languages.join(", "));
  }
  const size = formatSizeCap(profile.max_size_mb);
  if (size) {
    parts.push(size);
  }
  return parts.join(" · ") || profile.name;
}

export interface ChainRow {
  profileId: number;
  name: string;
  role: "principal" | "repli global";
}

export function describeEffectiveChain(
  chainIds: number[],
  profileById: Map<number, QualityProfileLike>,
  principalId: number | null,
  globalOrder: number[],
): { rows: ChainRow[]; hint: string } {
  const rows: ChainRow[] = chainIds.map((profileId, index) => {
    const profile = profileById.get(profileId);
    return {
      profileId,
      name: profile?.name ?? `Profil ${profileId}`,
      role: index === 0 ? "principal" : "repli global",
    };
  });
  const globalNames = globalOrder
    .map((id) => profileById.get(id)?.name ?? `Profil ${id}`)
    .filter(Boolean);
  let hint = "";
  if (globalNames.length && principalId != null) {
    const principalName = profileById.get(principalId)?.name ?? "principal";
    const dup = globalOrder.includes(principalId);
    hint = `Liste globale : ${globalNames.join(", puis ")}.`;
    if (dup) {
      hint += ` ${principalName} est déjà principal : il n'est pas répété.`;
    }
  } else if (globalNames.length === 0) {
    hint = "Liste globale vide : aucun profil de repli.";
  }
  return { rows, hint };
}
