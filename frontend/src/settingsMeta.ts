import type { SettingRow } from "./api";

export function settingByKey(rows: SettingRow[], key: string): SettingRow | undefined {
  return rows.find((row) => row.key === key);
}

export function settingBoundMin(rows: SettingRow[], key: string, fallback?: number): number | undefined {
  const row = settingByKey(rows, key);
  if (typeof row?.min === "number") {
    return row.min;
  }
  return fallback;
}

export function settingBoundMax(rows: SettingRow[], key: string, fallback?: number): number | undefined {
  const row = settingByKey(rows, key);
  if (typeof row?.max === "number") {
    return row.max;
  }
  return fallback;
}

export function settingDefault(rows: SettingRow[], key: string): string | number | boolean | null | undefined {
  return settingByKey(rows, key)?.default;
}

export function resolvedSettingNumber(row: SettingRow): number | undefined {
  if (typeof row.value === "number") {
    return row.value;
  }
  if (typeof row.default === "number") {
    return row.default;
  }
  return undefined;
}

export function applySettingNumber(rows: SettingRow[], key: string): number | undefined {
  const row = settingByKey(rows, key);
  if (!row) {
    return undefined;
  }
  return resolvedSettingNumber(row);
}

export function formatDefaultLabel(rows: SettingRow[], key: string): string {
  const value = settingDefault(rows, key);
  if (value === undefined || value === null || value === "") {
    return "";
  }
  return `Défaut ${value}`;
}

export function hintWithDefault(rows: SettingRow[], key: string, after: string): string {
  const label = formatDefaultLabel(rows, key);
  if (!label) {
    return after;
  }
  return `${label}. ${after}`;
}

export function hintWithDefaultUnit(
  rows: SettingRow[],
  key: string,
  unit: string,
  after: string,
): string {
  const value = settingDefault(rows, key);
  if (value === undefined || value === null || value === "") {
    return after;
  }
  return `Défaut ${value} ${unit}. ${after}`;
}

export function finiteNumber(value: number | undefined): number | undefined {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    return undefined;
  }
  return value;
}

/** Minutes depuis minuit pour l’UI quand la ref n’est pas encore chargée. */
export function minutesForUi(value: number | undefined): number {
  return finiteNumber(value) ?? 0;
}

/** Retire `undefined`, `null` et les nombres non finis avant PATCH / JSON. */
export function sanitizePatchValues(values: Record<string, unknown>): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const [key, value] of Object.entries(values)) {
    if (value === undefined || value === null) {
      continue;
    }
    if (typeof value === "number" && !Number.isFinite(value)) {
      continue;
    }
    out[key] = value;
  }
  return out;
}