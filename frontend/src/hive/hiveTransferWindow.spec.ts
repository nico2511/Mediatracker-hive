import { describe, expect, it } from "vitest";

import {
  hiveTransferMinutesToTime,
  hiveTransferTimeToMinutes,
  isHiveTransferAllDay,
  HIVE_TRANSFER_ALL_DAY_END,
  HIVE_TRANSFER_ALL_DAY_START,
} from "./hiveTransferWindow";

describe("hiveTransferWindow", () => {
  it("détecte la plage toute la journée", () => {
    expect(isHiveTransferAllDay(HIVE_TRANSFER_ALL_DAY_START, HIVE_TRANSFER_ALL_DAY_END)).toBe(true);
    expect(isHiveTransferAllDay(0, 1439)).toBe(false);
  });

  it("convertit minutes ↔ heure pour une plage classique", () => {
    expect(hiveTransferMinutesToTime(22 * 60)).toBe("22:00");
    expect(hiveTransferMinutesToTime(6 * 60 + 30)).toBe("06:30");
    expect(hiveTransferTimeToMinutes("22:00")).toBe(22 * 60);
    expect(hiveTransferTimeToMinutes("06:30")).toBe(6 * 60 + 30);
  });

  it("gère 1440 minutes comme fin de journée", () => {
    expect(hiveTransferMinutesToTime(HIVE_TRANSFER_ALL_DAY_END)).toBe("00:00");
    expect(hiveTransferTimeToMinutes("24:00")).toBe(HIVE_TRANSFER_ALL_DAY_END);
  });
});