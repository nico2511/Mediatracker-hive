import { describe, expect, it } from "vitest";

import type { SettingRow } from "./api";
import {
  applySettingNumber,
  formatDefaultLabel,
  sanitizePatchValues,
  settingBoundMin,
  settingBoundMax,
} from "./settingsMeta";

const rows: SettingRow[] = [
  {
    key: "source_error_backoff_min",
    value: 120,
    secret: false,
    set: true,
    min: 30,
    max: 1440,
    default: 180,
  },
];

describe("settingsMeta", () => {
  it("lit bornes et défaut depuis la réponse API", () => {
    expect(settingBoundMin(rows, "source_error_backoff_min")).toBe(30);
    expect(settingBoundMax(rows, "source_error_backoff_min")).toBe(1440);
    expect(formatDefaultLabel(rows, "source_error_backoff_min")).toBe("Défaut 180");
    expect(applySettingNumber(rows, "source_error_backoff_min")).toBe(120);
  });

  it("replie quand la clé ou les métadonnées manquent", () => {
    expect(settingBoundMin([], "source_error_backoff_min", 1)).toBe(1);
    expect(applySettingNumber(rows, "missing")).toBeUndefined();
  });

  it("sanitizePatchValues retire undefined, null et NaN", () => {
    const clean = sanitizePatchValues({
      ok: 5,
      missing: undefined,
      nil: null,
      bad: Number.NaN,
      text: "x",
    });
    expect(clean).toEqual({ ok: 5, text: "x" });
    expect(JSON.stringify(clean)).not.toContain("null");
  });
});