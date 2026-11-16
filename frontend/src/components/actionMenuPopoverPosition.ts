export const ACTION_MENU_VIEWPORT_MARGIN = 8;
export const ACTION_MENU_GAP = 6;

export type ActionMenuRect = {
  top: number;
  bottom: number;
  left: number;
  right: number;
  width: number;
};

export type ActionMenuPopoverStyle = {
  top: string;
  left: string;
  minWidth: string;
  maxHeight?: string;
  overflowY?: string;
};

export function viewportBottomLimit(viewportHeight: number, tabbarTop: number | null): number {
  let limit = viewportHeight - ACTION_MENU_VIEWPORT_MARGIN;
  if (tabbarTop != null && tabbarTop > ACTION_MENU_VIEWPORT_MARGIN) {
    limit = Math.min(limit, tabbarTop - ACTION_MENU_VIEWPORT_MARGIN);
  }
  return limit;
}

export function computeHorizontalPosition(
  trigger: ActionMenuRect,
  menuWidth: number,
  viewportWidth: number,
  cssMinWidth: number,
): { left: number; minWidth: number } {
  const alignRight =
    trigger.left + trigger.width / 2 > viewportWidth / 2 ||
    trigger.left + menuWidth > viewportWidth - ACTION_MENU_VIEWPORT_MARGIN;
  let left = alignRight ? trigger.right - menuWidth : trigger.left;
  left = Math.min(left, viewportWidth - ACTION_MENU_VIEWPORT_MARGIN - menuWidth);
  left = Math.max(left, ACTION_MENU_VIEWPORT_MARGIN);
  return { left, minWidth: cssMinWidth };
}

export function computeVerticalPosition(
  trigger: ActionMenuRect,
  menuHeight: number,
  viewportBottom: number,
): Pick<ActionMenuPopoverStyle, "top" | "maxHeight" | "overflowY"> {
  const spaceBelow = viewportBottom - (trigger.bottom + ACTION_MENU_GAP);
  const spaceAbove = trigger.top - ACTION_MENU_GAP - ACTION_MENU_VIEWPORT_MARGIN;

  let top: number;
  let maxHeight: number | undefined;

  if (menuHeight <= spaceBelow) {
    top = trigger.bottom + ACTION_MENU_GAP;
  } else if (menuHeight <= spaceAbove) {
    top = trigger.top - ACTION_MENU_GAP - menuHeight;
  } else if (spaceBelow >= spaceAbove) {
    top = trigger.bottom + ACTION_MENU_GAP;
    maxHeight = Math.max(0, Math.floor(spaceBelow));
  } else {
    maxHeight = Math.max(0, Math.floor(spaceAbove));
    top = Math.max(ACTION_MENU_VIEWPORT_MARGIN, trigger.top - ACTION_MENU_GAP - maxHeight);
  }

  const style: Pick<ActionMenuPopoverStyle, "top" | "maxHeight" | "overflowY"> = {
    top: `${top}px`,
  };
  if (maxHeight != null && maxHeight > 0 && menuHeight > maxHeight) {
    style.maxHeight = `${maxHeight}px`;
    style.overflowY = "auto";
  }
  return style;
}