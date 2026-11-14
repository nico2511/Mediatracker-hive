const PARIS_TIME: Intl.DateTimeFormatOptions = {
  timeZone: "Europe/Paris",
  dateStyle: "short",
  timeStyle: "short",
};

const PARIS_CLOCK: Intl.DateTimeFormatOptions = {
  timeZone: "Europe/Paris",
  hour: "2-digit",
  minute: "2-digit",
};

const PARIS_DAY_KEY = new Intl.DateTimeFormat("fr-CA", {
  timeZone: "Europe/Paris",
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
});

const PARIS_DAY_LABEL = new Intl.DateTimeFormat("fr-FR", {
  timeZone: "Europe/Paris",
  weekday: "long",
  day: "numeric",
  month: "long",
});

export function parseInstant(value: string | number | Date | null | undefined): Date | null {
  if (value == null) {
    return null;
  }
  if (value instanceof Date) {
    return Number.isNaN(value.getTime()) ? null : value;
  }
  if (typeof value === "number") {
    const parsed = new Date(value);
    return Number.isNaN(parsed.getTime()) ? null : parsed;
  }
  if (typeof value !== "string") {
    return null;
  }
  const trimmed = value.trim();
  const hasZone = /[zZ]$|[+-]\d{2}:\d{2}$/.test(trimmed);
  const normalized = hasZone ? trimmed : `${trimmed}Z`;
  const parsed = new Date(normalized);
  if (Number.isNaN(parsed.getTime())) {
    return null;
  }
  return parsed;
}

export function formatParis(value: string | number | Date | null | undefined): string {
  const parsed = parseInstant(value);
  if (!parsed) {
    return "";
  }
  return parsed.toLocaleString("fr-FR", PARIS_TIME);
}

export function formatParisClock(value: string | number | Date | null | undefined): string {
  const parsed = parseInstant(value);
  if (!parsed) {
    return "";
  }
  return parsed.toLocaleString("fr-FR", PARIS_CLOCK);
}

export function parisDayKey(value: string | null | undefined): string {
  const parsed = parseInstant(value);
  if (!parsed) {
    return "";
  }
  return PARIS_DAY_KEY.format(parsed);
}

export function formatParisDayHeading(key: string, todayKey: string, yesterdayKey: string): string {
  if (key === todayKey) {
    return "Aujourd'hui";
  }
  if (key === yesterdayKey) {
    return "Hier";
  }
  const parsed = new Date(`${key}T12:00:00`);
  if (Number.isNaN(parsed.getTime())) {
    return key;
  }
  const label = PARIS_DAY_LABEL.format(parsed);
  return label.charAt(0).toUpperCase() + label.slice(1);
}
