/** Accueil : limites fixes des sections poster (sans mesure de largeur). */

export const HOME_DESKTOP_POSTER_CAP = 7;
export const HOME_MOBILE_POSTER_CAP = 6;
export const HOME_EPISODE_SECTION_CAP = 4;

export function collapsedPosterLimit(
  wide: boolean,
  collapsedDesktop = HOME_DESKTOP_POSTER_CAP,
  collapsedMobile = HOME_MOBILE_POSTER_CAP,
): number {
  return wide ? collapsedDesktop : collapsedMobile;
}

export function visibleHomeItemCount(
  itemsLength: number,
  expanded: boolean,
  wide: boolean,
  collapsedDesktop = HOME_DESKTOP_POSTER_CAP,
  collapsedMobile = HOME_MOBILE_POSTER_CAP,
): number {
  if (expanded) {
    return itemsLength;
  }
  const cap = collapsedPosterLimit(wide, collapsedDesktop, collapsedMobile);
  return Math.min(itemsLength, cap);
}

export function hiddenHomeItemCount(
  itemsLength: number,
  expanded: boolean,
  wide: boolean,
  collapsedDesktop = HOME_DESKTOP_POSTER_CAP,
  collapsedMobile = HOME_MOBILE_POSTER_CAP,
): number {
  if (expanded) {
    return 0;
  }
  const visible = visibleHomeItemCount(itemsLength, expanded, wide, collapsedDesktop, collapsedMobile);
  return Math.max(0, itemsLength - visible);
}
