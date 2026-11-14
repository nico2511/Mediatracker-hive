/** Normalise les valeurs de réglages API (string, nombre, liste, null…). */

export function settingAsString(value: unknown): string {
  if (value == null) {
    return "";
  }
  if (typeof value === "string") {
    return value;
  }
  if (typeof value === "number" || typeof value === "boolean") {
    return String(value);
  }
  return "";
}

export function settingAsCommaList(value: unknown): string {
  if (Array.isArray(value)) {
    return value
      .map((entry) => String(entry ?? "").trim())
      .filter(Boolean)
      .join(",");
  }
  return settingAsString(value);
}

export function settingCommaTokens(value: unknown): string[] {
  const text = settingAsCommaList(value);
  return text
    .split(",")
    .map((part) => part.trim().toLowerCase())
    .filter(Boolean);
}

export function settingAsLineList(value: unknown): string[] {
  if (Array.isArray(value)) {
    return value.map((entry) => String(entry ?? "").trim()).filter(Boolean);
  }
  return settingAsString(value)
    .split("\n")
    .map((part) => part.trim())
    .filter(Boolean);
}
