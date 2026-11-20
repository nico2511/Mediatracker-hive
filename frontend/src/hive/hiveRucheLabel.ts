/** Libellé liste ruche (aligné réglages Hive, sans nom dédié côté API). */
export type HiveRucheListRow = {
  ruche_id_hex: string;
  member_count?: number;
  is_creator?: boolean;
};

export function hiveRucheLabel(r: HiveRucheListRow): string {
  const short = r.ruche_id_hex.slice(0, 8);
  const members =
    typeof r.member_count === "number" ? ` · ${r.member_count} membre${r.member_count > 1 ? "s" : ""}` : "";
  const creator = r.is_creator ? " (créateur)" : "";
  return `Ruche ${short}…${members}${creator}`;
}

/** Nom court attendu pour confirmer une action destructive sur la ruche. */
export function hiveRucheConfirmName(rucheIdHex: string): string {
  return `Ruche ${rucheIdHex.slice(0, 8)}`;
}
