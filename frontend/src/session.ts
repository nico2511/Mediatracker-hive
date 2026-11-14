import { api, setUnauthorized, shouldRestorePersistedSession, type User } from "./api";
import { clearOfflineUserData } from "./pwa/clearOfflineUserData";
import { invalidateSettings } from "./settings";

export interface SessionGate {
  signedIn: boolean;
  user: User | null;
  needsPassword: boolean;
  setupCompleted: boolean;
}

const SESSION_PERSIST_KEY = "mt.session.gate.v1";

let cached: SessionGate | null = null;
let inflight: Promise<SessionGate> | null = null;

function readPersistedGate(): SessionGate | null {
  if (typeof localStorage === "undefined") {
    return null;
  }
  try {
    const raw = localStorage.getItem(SESSION_PERSIST_KEY);
    if (!raw) {
      return null;
    }
    const parsed = JSON.parse(raw) as SessionGate;
    if (!parsed || typeof parsed !== "object" || !parsed.signedIn || !parsed.user) {
      return null;
    }
    return parsed;
  } catch {
    return null;
  }
}

function writePersistedGate(gate: SessionGate): void {
  if (typeof localStorage === "undefined" || !gate.signedIn || !gate.user) {
    return;
  }
  try {
    localStorage.setItem(SESSION_PERSIST_KEY, JSON.stringify(gate));
  } catch {
    // ignore quota / private mode
  }
}

function clearPersistedGate(): void {
  if (typeof localStorage === "undefined") {
    return;
  }
  try {
    localStorage.removeItem(SESSION_PERSIST_KEY);
  } catch {
    // ignore
  }
}

export function clearSession(): void {
  cached = null;
  inflight = null;
  invalidateSettings();
}

/** Déconnexion ou session expirée : invalide la session et efface les données hors ligne locales. */
export function clearSessionAndOfflineData(): void {
  clearSession();
  clearPersistedGate();
  void clearOfflineUserData();
}

export function noteSetupCompleted(): void {
  if (cached?.signedIn) {
    cached = { ...cached, setupCompleted: true };
    writePersistedGate(cached);
    return;
  }
  clearSession();
}

export function sessionUser(): User | null {
  return cached?.user ?? null;
}

export async function sessionGate(): Promise<SessionGate> {
  if (cached) {
    return cached;
  }
  if (!inflight) {
    inflight = load().finally(() => {
      inflight = null;
    });
  }
  return inflight;
}

async function load(): Promise<SessionGate> {
  let user: User | null = null;
  let meError: unknown = null;
  try {
    user = await api<User>("/api/v1/auth/me");
  } catch (err) {
    meError = err;
    user = null;
  }
  if (!user) {
    const persisted = readPersistedGate();
    if (persisted?.signedIn && shouldRestorePersistedSession(meError)) {
      cached = persisted;
      return cached;
    }
    try {
      const status = await api<{ needs_password: boolean; completed: boolean }>("/api/v1/setup/status");
      cached = {
        signedIn: false,
        user: null,
        needsPassword: status.needs_password,
        setupCompleted: status.completed,
      };
    } catch {
      cached = { signedIn: false, user: null, needsPassword: false, setupCompleted: false };
    }
    return cached;
  }
  try {
    const status = await api<{ completed: boolean; needs_password: boolean }>("/api/v1/setup/status");
    cached = {
      signedIn: true,
      user,
      needsPassword: status.needs_password,
      setupCompleted: status.completed,
    };
  } catch {
    cached = { signedIn: true, user, needsPassword: false, setupCompleted: true };
  }
  writePersistedGate(cached);
  return cached;
}

setUnauthorized(() => {
  clearSessionAndOfflineData();
});
