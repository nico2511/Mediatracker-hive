/** Lecture non validante du document d’invitation (le backend vérifie les signatures). */

const LINK_PREFIX = "mtrhive:1:";

function b64urlDecode(text: string): string {
  const pad = "=".repeat((4 - (text.length % 4)) % 4);
  const b64 = (text + pad).replace(/-/g, "+").replace(/_/g, "/");
  return atob(b64);
}

export interface InvitationLinkFields {
  ruche_id_hex: string;
  creator_id: string;
  creator_address: string;
}

export function parseInvitationLink(link: string): InvitationLinkFields | null {
  const trimmed = link.trim();
  if (!trimmed.startsWith(LINK_PREFIX)) {
    return null;
  }
  try {
    const raw = b64urlDecode(trimmed.slice(LINK_PREFIX.length));
    const outer = JSON.parse(raw) as Record<string, unknown>;
    if (outer.v !== 1) {
      return null;
    }
    const rucheId = String(outer.ruche_id || "");
    const creatorId = String(outer.creator_id || "");
    const creatorAddress = String(outer.creator_address || "");
    if (!rucheId || !creatorId) {
      return null;
    }
    return {
      ruche_id_hex: rucheId,
      creator_id: creatorId,
      creator_address: creatorAddress,
    };
  } catch {
    return null;
  }
}
