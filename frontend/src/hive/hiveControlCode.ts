/** Normalisation du code de contrôle Hive (base32, 8 caractères). */

export function normalizeHiveControlCode(raw: string): string {
  return raw.trim().toUpperCase().replace(/[^A-Z2-7]/g, "");
}

export function hiveControlCodesMatch(typed: string, expected: string | null | undefined): boolean {
  const normExpected = normalizeHiveControlCode(expected || "");
  const normTyped = normalizeHiveControlCode(typed);
  if (!normExpected || normTyped.length !== normExpected.length) {
    return false;
  }
  return normTyped === normExpected;
}
