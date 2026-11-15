export const PROFILE_FALLBACK_ORDER_KEY = "profile_fallback_order";
export const GLOBAL_ALLOW_FALLBACK_KEY = "allow_fallback";

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

function chainIds(principalId: number, fallbackIds: number[]): number[] {
  const chain = [principalId];
  const seen = new Set(chain);
  for (const profileId of fallbackIds) {
    if (profileId === principalId || seen.has(profileId)) {
      continue;
    }
    seen.add(profileId);
    chain.push(profileId);
  }
  return chain;
}

/** Chaîne effective : global (NULL côté API) ou liste personnalisée (éventuellement vide). */
export function resolveEffectiveChainIds(
  principalId: number | null,
  globalOrder: number[],
  globalAllowFallback: boolean,
  mediaFallbackIds: number[] | null,
): number[] {
  if (principalId == null || principalId <= 0) {
    return [];
  }
  if (mediaFallbackIds === null) {
    const fallbacks = globalAllowFallback ? globalOrder : [];
    return chainIds(principalId, fallbacks);
  }
  return chainIds(principalId, mediaFallbackIds);
}

/** @deprecated utiliser resolveEffectiveChainIds */
export function buildEffectiveProfileChain(
  principalId: number | null,
  globalOrder: number[],
  allowFallback: boolean,
): number[] {
  return resolveEffectiveChainIds(principalId, globalOrder, allowFallback, null);
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

export type ChainRowRole = "principal" | "repli global" | "repli fiche";

export interface ChainRow {
  profileId: number;
  name: string;
  role: ChainRowRole;
}

export function describeEffectiveChain(
  chainIds: number[],
  profileById: Map<number, QualityProfileLike>,
  options: {
    usesGlobal: boolean;
    globalAllowFallback: boolean;
    globalOrder: number[];
    principalId: number | null;
  },
): { rows: ChainRow[]; hint: string } {
  const { usesGlobal, globalAllowFallback, globalOrder, principalId } = options;
  const rows: ChainRow[] = chainIds.map((profileId, index) => {
    const profile = profileById.get(profileId);
    let role: ChainRowRole = "principal";
    if (index > 0) {
      role = usesGlobal ? "repli global" : "repli fiche";
    }
    return {
      profileId,
      name: profile?.name ?? `Profil ${profileId}`,
      role,
    };
  });
  let hint = "";
  if (usesGlobal) {
    if (!globalAllowFallback) {
      hint = "Repli global désactivé : seul le profil principal est essayé.";
    } else if (globalOrder.length === 0) {
      hint = "Ordre global vide : seul le profil principal est essayé.";
    } else {
      const globalNames = globalOrder.map((id) => profileById.get(id)?.name ?? `Profil ${id}`);
      hint = `Réglage global : repli Oui. Ordre global après le principal : ${globalNames.join(", puis ")}.`;
      if (principalId != null && globalOrder.includes(principalId)) {
        const principalName = profileById.get(principalId)?.name ?? "principal";
        hint += ` ${principalName} est déjà principal : il n'est pas répété.`;
      }
    }
  } else if (chainIds.length <= 1) {
    hint = "Chaîne personnalisée sans repli : seul le profil principal est essayé.";
  } else {
    hint = "Chaîne personnalisée pour cette fiche (ordre ci-dessus).";
  }
  return { rows, hint };
}

export function toggleCustomFallbackSelection(
  ordered: number[],
  profileId: number,
  principalId: number | null,
  checked: boolean,
): number[] {
  if (principalId != null && profileId === principalId) {
    return ordered;
  }
  if (checked) {
    return ordered.includes(profileId) ? ordered : [...ordered, profileId];
  }
  return ordered.filter((id) => id !== profileId);
}

export function moveCustomFallback(ordered: number[], profileId: number, delta: number): number[] {
  const index = ordered.indexOf(profileId);
  if (index < 0) {
    return ordered;
  }
  const next = index + delta;
  if (next < 0 || next >= ordered.length) {
    return ordered;
  }
  const copy = [...ordered];
  const [row] = copy.splice(index, 1);
  copy.splice(next, 0, row);
  return copy;
}