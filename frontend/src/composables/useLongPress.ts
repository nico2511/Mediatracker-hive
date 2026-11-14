import { onBeforeUnmount } from "vue";

/** Durée d'appui avant l'ouverture du menu de carte. */
export const LONG_PRESS_MS = 450;
/** Au-delà de ce déplacement du doigt, l'appui long est annulé (défilement, glisser). */
const MOVE_TOLERANCE_PX = 10;

export interface PressPoint {
  x: number;
  y: number;
  /** "touch" | "pen" | "mouse" | "keyboard" */
  via: string;
}

/**
 * Appui long tactile (minuteur), clic droit et touche Menu / Maj+F10 (événement contextmenu).
 * Empêche le menu natif d'Android et le clic qui suit un appui long.
 */
export function useLongPress(trigger: (at: PressPoint) => void) {
  let timer: number | undefined;
  let start: { x: number; y: number; id: number; type: string } | null = null;
  let fired = false;

  function cancel() {
    if (timer !== undefined) window.clearTimeout(timer);
    timer = undefined;
    start = null;
  }

  function onPointerdown(event: PointerEvent) {
    fired = false;
    if (event.pointerType === "mouse" || !event.isPrimary) return;
    start = { x: event.clientX, y: event.clientY, id: event.pointerId, type: event.pointerType };
    timer = window.setTimeout(() => {
      if (!start) return;
      fired = true;
      navigator.vibrate?.(10);
      trigger({ x: start.x, y: start.y, via: start.type });
      cancel();
    }, LONG_PRESS_MS);
  }

  function onPointermove(event: PointerEvent) {
    if (!start || event.pointerId !== start.id) return;
    if (Math.hypot(event.clientX - start.x, event.clientY - start.y) > MOVE_TOLERANCE_PX) cancel();
  }

  function onContextmenu(event: MouseEvent) {
    // Android déclenche contextmenu vers 500 ms : on bloque le menu natif (image, lien).
    event.preventDefault();
    if (fired) return;
    const touch = start !== null;
    cancel();
    fired = touch;
    const keyboard = event.clientX === 0 && event.clientY === 0;
    trigger({ x: event.clientX, y: event.clientY, via: keyboard ? "keyboard" : "mouse" });
  }

  /** À poser en capture : le relâchement après un appui long ne doit pas ouvrir l'aperçu. */
  function onClickCapture(event: MouseEvent) {
    if (!fired) return;
    fired = false;
    event.preventDefault();
    event.stopPropagation();
  }

  /** Après un appui long, annule le mousedown/clic de compatibilité : il viserait le fond du menu. */
  function onTouchend(event: TouchEvent) {
    if (!fired) return;
    fired = false;
    if (event.cancelable) event.preventDefault();
  }

  onBeforeUnmount(cancel);

  return {
    onPointerdown,
    onPointermove,
    onPointerup: cancel,
    onTouchend,
    onPointercancel: cancel,
    onContextmenu,
    onClickCapture,
  };
}
