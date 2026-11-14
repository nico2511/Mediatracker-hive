"""Session hors ligne : ne pas déconnecter sur erreurs réseau / 5xx / faux 401."""

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FRONT = ROOT / "frontend"
FRONT_SRC = FRONT / "src"

_NAV_POLYFILL = """
if (typeof globalThis.navigator === "undefined") {
  Object.defineProperty(globalThis, "navigator", { configurable: true, value: { onLine: true } });
}
"""


def _tsx(script: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["npx", "--yes", "tsx", "-e", script],
        cwd=FRONT_SRC,
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )


def test_authoritative_api_401_requires_online_json_fingerprint():
    script = _NAV_POLYFILL + r"""
import { isAuthoritativeApi401 } from "./api.ts";

const apiHeaders = new Headers({
  "Content-Type": "application/problem+json",
  "X-Content-Type-Options": "nosniff",
});

const ok = isAuthoritativeApi401(
  new Response(JSON.stringify({ detail: "Session invalide." }), { status: 401, headers: apiHeaders }),
);
if (!ok) {
  throw new Error("expected authoritative 401");
}

const html401 = isAuthoritativeApi401(
  new Response("<html></html>", { status: 401, headers: { "Content-Type": "text/html" } }),
);
if (html401) {
  throw new Error("html 401 must not count");
}

const jsonNoFingerprint = isAuthoritativeApi401(
  new Response("{}", { status: 401, headers: { "Content-Type": "application/json" } }),
);
if (jsonNoFingerprint) {
  throw new Error("json without nosniff must not count");
}

Object.defineProperty(globalThis.navigator, "onLine", { configurable: true, value: false });
const offline = isAuthoritativeApi401(
  new Response("{}", { status: 401, headers: apiHeaders }),
);
if (offline) {
  throw new Error("offline 401 must not count");
}
"""
    proc = _tsx(script)
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_unauthorized_handler_only_on_real_401_online():
    script = _NAV_POLYFILL + r"""
import { api, setUnauthorized, ApiError } from "./api.ts";

let unauthorizedCalls = 0;
setUnauthorized(() => {
  unauthorizedCalls += 1;
});

const apiHeaders = {
  "Content-Type": "application/problem+json",
  "X-Content-Type-Options": "nosniff",
};

const originalFetch = globalThis.fetch;
Object.defineProperty(globalThis.navigator, "onLine", { configurable: true, value: true });

async function mockFetch(input, init) {
  const url = typeof input === "string" ? input : input.url;
  if (url.endsWith("/api/v1/auth/me")) {
    return new Response(JSON.stringify({ detail: "Session invalide." }), { status: 401, headers: apiHeaders });
  }
  if (url.endsWith("/api/v1/dashboard")) {
    return new Response(JSON.stringify({ detail: "down" }), { status: 503, headers: apiHeaders });
  }
  throw new TypeError("Failed to fetch");
}

globalThis.fetch = mockFetch;

(async () => {
try {
  await api("/api/v1/auth/me").catch(() => {});
  if (unauthorizedCalls !== 1) {
    throw new Error(`expected 1 unauthorized, got ${unauthorizedCalls}`);
  }
  unauthorizedCalls = 0;
  await api("/api/v1/dashboard").catch((err) => {
    if (!(err instanceof ApiError) || err.status !== 503) {
      throw err;
    }
  });
  if (unauthorizedCalls !== 0) {
    throw new Error(`503 must not unauthorized, got ${unauthorizedCalls}`);
  }
  unauthorizedCalls = 0;
  await api("/api/v1/other").catch(() => {});
  if (unauthorizedCalls !== 0) {
    throw new Error(`network error must not unauthorized, got ${unauthorizedCalls}`);
  }
} finally {
  globalThis.fetch = originalFetch;
}
})().then(() => process.exit(0)).catch((e) => { console.error(e); process.exit(1); });
"""
    proc = _tsx(script)
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_session_restores_persisted_gate_when_offline():
    script = _NAV_POLYFILL + r"""
import { shouldRestorePersistedSession, ApiError } from "./api.ts";

Object.defineProperty(globalThis.navigator, "onLine", { configurable: true, value: false });
if (!shouldRestorePersistedSession(new TypeError("Failed to fetch"))) {
  throw new Error("offline network");
}
Object.defineProperty(globalThis.navigator, "onLine", { configurable: true, value: true });
if (!shouldRestorePersistedSession(new ApiError(503, "Service Unavailable"))) {
  throw new Error("503 online");
}
if (!shouldRestorePersistedSession(new ApiError(401, "Unauthorized"))) {
  throw new Error("non-authoritative 401 should allow restore");
}
if (shouldRestorePersistedSession(new ApiError(403, "Forbidden"))) {
  throw new Error("403 should not restore");
}
"""
    proc = _tsx(script)
    assert proc.returncode == 0, proc.stdout + proc.stderr
