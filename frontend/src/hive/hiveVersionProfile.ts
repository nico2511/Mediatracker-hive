export interface HiveVersionProfileRow {
  profile_compatible?: boolean;
  recommended?: boolean;
}

export function hiveVersionProfileCompatible(ver: HiveVersionProfileRow | null | undefined): boolean {
  if (!ver) {
    return false;
  }
  if (ver.profile_compatible != null) {
    return Boolean(ver.profile_compatible);
  }
  return Boolean(ver.recommended);
}

export function hiveVersionRecommended(ver: HiveVersionProfileRow | null | undefined): boolean {
  return Boolean(ver?.recommended);
}