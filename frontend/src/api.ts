function problemText(problem: unknown, fallback: string): string {
  if (!problem || typeof problem !== "object") {
    return fallback;
  }
  const body = problem as { detail?: unknown; errors?: { msg?: string }[] };
  const extras = Array.isArray(body.errors)
    ? body.errors.map((item) => item?.msg).filter((item): item is string => Boolean(item))
    : [];
  if (Array.isArray(body.detail)) {
    const lines = body.detail.map((item) => {
      if (item && typeof item === "object" && "msg" in item) {
        return String((item as { msg: unknown }).msg);
      }
      return String(item);
    });
    return lines.filter(Boolean).join(" ") || extras.join(" ") || fallback;
  }
  if (typeof body.detail === "string" && body.detail) {
    if (extras.length && body.detail === "Le corps ou les paramètres sont invalides.") {
      return `${body.detail} ${extras.join(" ")}`;
    }
    return body.detail;
  }
  if (extras.length) {
    return extras.join(" ");
  }
  return fallback;
}

export class ApiError extends Error {
  status: number;
  correlationId?: string;
  code?: string;
  problem?: Record<string, unknown>;
  retryAfterSeconds?: number;

  constructor(
    status: number,
    message: string,
    correlationId?: string,
    code?: string,
    problem?: Record<string, unknown>,
    retryAfterSeconds?: number,
  ) {
    super(message);
    this.status = status;
    this.correlationId = correlationId;
    this.code = code;
    this.problem = problem;
    this.retryAfterSeconds = retryAfterSeconds;
  }
}

/** Délai avant nouvel essai (entête Retry-After HTTP), sans plafond artificiel. */
export function parseRetryAfterSeconds(header: string | null, fallback = 5): number {
  if (!header || !header.trim()) {
    return fallback;
  }
  const raw = header.trim();
  const asNumber = Number(raw);
  if (Number.isFinite(asNumber) && asNumber >= 0) {
    return asNumber;
  }
  const parsed = Date.parse(raw);
  if (!Number.isNaN(parsed)) {
    const delay = (parsed - Date.now()) / 1000;
    return Math.max(0, delay);
  }
  return fallback;
}

let onUnauthorized: (() => void) | null = null;

export function setUnauthorized(handler: () => void): void {
  onUnauthorized = handler;
}

function csrfToken(): string {
  const match = document.cookie.match(/(?:^|; )mt_csrf=([^;]*)/);
  return match ? decodeURIComponent(match[1]) : "";
}

const AUTH_LOGIN_PATH = "/api/v1/auth/login";

function isSessionExpiry401(path: string, status: number): boolean {
  if (status !== 401) {
    return false;
  }
  const bare = path.split("?")[0];
  return bare !== AUTH_LOGIN_PATH;
}

const STATUS_USER_MESSAGE: Record<number, string> = {
  401: "Session expirée.",
  403: "Action non autorisée.",
  404: "Introuvable.",
  409: "Conflit.",
  413: "Fichier trop volumineux pour le serveur ou le proxy inverse.",
  422: "Données invalides.",
  429: "Trop de tentatives.",
};

const ENGLISH_STATUS_TEXT = new Set([
  "Bad Request",
  "Unauthorized",
  "Forbidden",
  "Not Found",
  "Method Not Allowed",
  "Conflict",
  "Unprocessable Entity",
  "Too Many Requests",
  "Internal Server Error",
  "Bad Gateway",
  "Service Unavailable",
  "Gateway Timeout",
]);

function looksTechnicalApiMessage(message: string): boolean {
  const trimmed = message.trim();
  if (!trimmed) {
    return true;
  }
  if (ENGLISH_STATUS_TEXT.has(trimmed)) {
    return true;
  }
  if (/request failed with status code/i.test(trimmed)) {
    return true;
  }
  if (/^failed to fetch$/i.test(trimmed)) {
    return true;
  }
  if (/^networkerror/i.test(trimmed)) {
    return true;
  }
  if (/^[A-Za-z]+Error:/.test(trimmed)) {
    return true;
  }
  if (/^\s*[{[]/.test(trimmed)) {
    return true;
  }
  if (/\bis not a valid\b/i.test(trimmed)) {
    return true;
  }
  if (/\bfield required\b/i.test(trimmed)) {
    return true;
  }
  if (/^input should be\b/i.test(trimmed)) {
    return true;
  }
  return false;
}

function looksUserFrench(message: string): boolean {
  if (looksTechnicalApiMessage(message)) {
    return false;
  }
  if (/[àâäéèêëïîôùûüçœæÀÂÄÉÈÊËÏÎÔÙÛÜŒÆ]/.test(message)) {
    return true;
  }
  if (
    /^(Le |La |Les |L'|Un |Une |Aucun|Impossible|Cette |Ce |Vous |Il |Elle |Déjà|Connectez|Reconnectez|Base |Alignez)/i.test(
      message,
    )
  ) {
    return true;
  }
  return false;
}

export function isNetworkError(err: unknown): boolean {
  if (err instanceof TypeError) {
    return true;
  }
  if (err instanceof Error && /failed to fetch|networkerror|load failed/i.test(err.message)) {
    return true;
  }
  return false;
}

const TRANSIENT_HTTP_STATUSES = new Set([502, 503, 504]);

export function isTransientHttpStatus(status: number): boolean {
  return TRANSIENT_HTTP_STATUSES.has(status);
}

export function isBrowserOnline(): boolean {
  if (typeof navigator === "undefined") {
    return true;
  }
  return navigator.onLine !== false;
}

/** Vrai 401 JSON renvoyé par l'API MediaTracker (pas proxy / page hors ligne). */
export function isAuthoritativeApi401(response: Response): boolean {
  if (!isBrowserOnline()) {
    return false;
  }
  if (response.status !== 401) {
    return false;
  }
  if (response.type === "opaque" || response.type === "error") {
    return false;
  }
  const contentType = (response.headers.get("Content-Type") ?? "").toLowerCase();
  if (!contentType.includes("json")) {
    return false;
  }
  if (response.headers.get("X-Content-Type-Options")?.toLowerCase() !== "nosniff") {
    return false;
  }
  return true;
}

export function shouldRestorePersistedSession(err: unknown): boolean {
  if (!isBrowserOnline()) {
    return true;
  }
  if (isNetworkError(err)) {
    return true;
  }
  if (err instanceof ApiError) {
    if (isTransientHttpStatus(err.status)) {
      return true;
    }
    if (err.status === 401) {
      return true;
    }
  }
  return false;
}

/** Message français pour l'interface ; le détail technique reste dans la console. */
export function userFacingApiError(err: unknown, fallback: string): string {
  console.error("[API]", err);
  if (isNetworkError(err)) {
    return "Serveur injoignable.";
  }
  if (err instanceof ApiError) {
    const msg = err.message.trim();
    if (looksUserFrench(msg)) {
      return msg;
    }
    if (err.status === 401 && msg && !looksTechnicalApiMessage(msg)) {
      return msg;
    }
    const mapped = STATUS_USER_MESSAGE[err.status];
    if (mapped) {
      return mapped;
    }
    if (err.status >= 500) {
      if (err.correlationId) {
        const base = looksUserFrench(msg) ? msg : fallback;
        if (base.includes(err.correlationId)) {
          return base;
        }
        return `${base} (réf. ${err.correlationId})`;
      }
      if (looksUserFrench(msg)) {
        return msg;
      }
      return "Erreur du serveur, consultez le Journal.";
    }
    return fallback;
  }
  return fallback;
}

export async function api<T>(path: string, options: RequestInit = {}): Promise<T> {
  const headers = new Headers(options.headers);
  headers.set("Accept", "application/json");
  if (options.body && !headers.has("Content-Type") && !(options.body instanceof FormData)) {
    headers.set("Content-Type", "application/json");
  }
  const method = (options.method || "GET").toUpperCase();
  if (!["GET", "HEAD", "OPTIONS"].includes(method)) {
    headers.set("X-CSRF-Token", csrfToken());
  }
  const response = await fetch(path, { ...options, headers, credentials: "include" });
  if (!response.ok) {
    if (isSessionExpiry401(path, response.status) && isAuthoritativeApi401(response)) {
      onUnauthorized?.();
    }
    let detail = response.statusText;
    let correlationId: string | undefined;
    let problemCode: string | undefined;
    let problem: Record<string, unknown> | undefined;
    try {
      const body = await response.json();
      if (body && typeof body === "object") {
        problem = body as Record<string, unknown>;
        if (typeof (body as { correlation_id?: unknown }).correlation_id === "string") {
          correlationId = (body as { correlation_id: string }).correlation_id;
        }
        if (typeof (body as { code?: unknown }).code === "string") {
          problemCode = (body as { code: string }).code;
        }
      }
      detail = problemText(body, detail);
    } catch {
      /* corps vide */
    }
    const retryAfterSeconds =
      response.status === 503 || response.status === 429
        ? parseRetryAfterSeconds(response.headers.get("Retry-After"))
        : undefined;
    throw new ApiError(response.status, detail, correlationId, problemCode, problem, retryAfterSeconds);
  }
  if (response.status === 204) {
    return undefined as T;
  }
  return response.json() as Promise<T>;
}

export interface User {
  id: number;
  username: string;
}

export interface SettingRow {
  key: string;
  value: string | number | boolean | null;
  secret: boolean;
  set: boolean;
}
