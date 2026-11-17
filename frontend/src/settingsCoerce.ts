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

/** Réordonne les jetons visibles sans retirer ceux masqués du modelValue. */
export function mergeCommaOrderPreservingHidden(
  modelValue: string,
  visibleIds: Iterable<string>,
  visibleOrder: string[],
): string {
  const full = settingCommaTokens(modelValue);
  const visible = new Set(visibleIds);
  const enabled = new Set(full);
  const visibleReorder = visibleOrder.filter((id) => enabled.has(id));
  let vi = 0;
  const merged: string[] = [];
  for (const id of full) {
    if (!visible.has(id)) {
      merged.push(id);
      continue;
    }
    if (vi < visibleReorder.length) {
      merged.push(visibleReorder[vi++]);
    }
  }
  const missing = visibleReorder.filter((id) => !merged.includes(id));
  return [...merged, ...missing].join(",");
}
