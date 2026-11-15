export const HIVE_TRANSFER_ALL_DAY_START = 0;
export const HIVE_TRANSFER_ALL_DAY_END = 24 * 60;

export function isHiveTransferAllDay(startMinutes: number, endMinutes: number): boolean {
  return startMinutes === HIVE_TRANSFER_ALL_DAY_START && endMinutes === HIVE_TRANSFER_ALL_DAY_END;
}

/** Convertit des minutes depuis minuit en valeur `HH:MM` pour input type=time. */
export function hiveTransferMinutesToTime(minutes: number): string {
  if (!Number.isFinite(minutes)) {
    return "00:00";
  }
  const clamped = Math.max(0, Math.min(24 * 60, Math.trunc(minutes)));
  if (clamped >= 24 * 60) {
    return "00:00";
  }
  const hours = Math.floor(clamped / 60);
  const mins = clamped % 60;
  return `${String(hours).padStart(2, "0")}:${String(mins).padStart(2, "0")}`;
}

/** Parse une valeur `HH:MM` ; retourne des minutes dans [0, 24×60]. */
export function hiveTransferTimeToMinutes(value: string): number {
  const trimmed = value.trim();
  const match = /^(\d{1,2}):(\d{2})$/.exec(trimmed);
  if (!match) {
    return 0;
  }
  const hours = Number(match[1]);
  const mins = Number(match[2]);
  if (!Number.isFinite(hours) || !Number.isFinite(mins) || mins > 59 || hours > 24) {
    return 0;
  }
  if (hours === 24) {
    return 24 * 60;
  }
  return hours * 60 + mins;
}

/**
 * Parse l’heure de fin : `00:00` avec un début après minuit signifie 24×60 (fin de journée),
 * car les inputs `type=time` ne peuvent pas afficher 24:00.
 */
export function hiveTransferEndTimeToMinutes(endValue: string, startMinutes: number): number {
  const parsed = hiveTransferTimeToMinutes(endValue);
  if (
    parsed === HIVE_TRANSFER_ALL_DAY_START &&
    endValue.trim() === "00:00" &&
    startMinutes > HIVE_TRANSFER_ALL_DAY_START
  ) {
    return HIVE_TRANSFER_ALL_DAY_END;
  }
  return parsed;
}