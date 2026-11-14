"""PWA hors ligne : file IndexedDB, build manifeste et icônes."""

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FRONT = ROOT / "frontend"
DIST = FRONT / "dist"


def _tsx(script: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["npx", "--yes", "tsx", "-e", script],
        cwd=FRONT,
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )


def test_offline_queue_duplicates_expiry_and_alternate_type():
    script = r"""
import { addQueueItem, findQueueDuplicate, isExpired, purgeExpiredQueue, QUEUE_RETENTION_MS } from "./src/pwa/offlineQueue.ts";
import { alternateFollowType } from "./src/pwa/parseTmdbInput.ts";

class MemoryStorage {
  store = new Map();
  get(key) { return this.store.get(key) ?? null; }
  set(key, value) { this.store.set(key, value); }
  delete(key) { this.store.delete(key); }
  clear() { this.store.clear(); }
}

const requests = new Map();
class FDBRequest {
  result = undefined;
  error = null;
  onsuccess = null;
  onerror = null;
  _resolve(result) {
    this.result = result;
    this.onsuccess?.();
  }
  _reject(err) {
    this.error = err;
    this.onerror?.();
  }
}

class FDBIndex {
  constructor(store, name) {
    this.store = store;
    this.name = name;
  }
  get(key) {
    const req = new FDBRequest();
    const hit = [...this.store.data.values()].find((row) => row.tmdb_id === key[0] && row.type === key[1]);
    setTimeout(() => req._resolve(hit ?? undefined), 0);
    return req;
  }
}

class FDBObjectStore {
  constructor(tx, name) {
    this.tx = tx;
    this.name = name;
    this.data = tx.db?.tables?.get(name) ?? tx.store ?? new Map();
    if (tx.db) {
      tx.db.tables.set(name, this.data);
    }
    this.indexes = { by_tmdb_type: new FDBIndex(this, "by_tmdb_type") };
  }
  index(name) { return this.indexes[name]; }
  add(value) {
    const req = new FDBRequest();
    const dup = [...this.data.values()].find((row) => row.tmdb_id === value.tmdb_id && row.type === value.type);
    if (dup) {
      setTimeout(() => req._reject(new Error("duplicate")), 0);
      return req;
    }
    this.data.set(value.id, value);
    setTimeout(() => req._resolve(value.id), 0);
    return req;
  }
  put(value) {
    const req = new FDBRequest();
    this.data.set(value.id, value);
    setTimeout(() => req._resolve(value.id), 0);
    return req;
  }
  delete(key) {
    const req = new FDBRequest();
    this.data.delete(key);
    setTimeout(() => req._resolve(), 0);
    return req;
  }
  get(key) {
    const req = new FDBRequest();
    setTimeout(() => req._resolve(this.data.get(key)), 0);
    return req;
  }
  getAll() {
    const req = new FDBRequest();
    setTimeout(() => req._resolve([...this.data.values()]), 0);
    return req;
  }
}

class FDBTransaction {
  constructor(db, name, mode) {
    this.db = db;
    this.mode = mode;
    this.store = new FDBObjectStore(this, name);
    this.oncomplete = null;
    this.onerror = null;
        setTimeout(() => this.oncomplete?.(), 5);
  }
  objectStore() { return this.store; }
}

class FDBDatabase {
  tables = new Map();
  objectStoreNames = { contains: (n) => this.tables.has(n) };
  onversionchange = null;
  close() {}
  createObjectStore(name) {
    const map = new Map();
    this.tables.set(name, map);
    const store = new FDBObjectStore({ db: this, store: map }, name);
    store.createIndex = () => ({});
    return store;
  }
  transaction(name, mode) {
    return new FDBTransaction(this, name, mode);
  }
}

const dbs = new Map();
globalThis.indexedDB = {
  open(name, version) {
    const req = new FDBRequest();
    const db = dbs.get(name) ?? new FDBDatabase();
    dbs.set(name, db);
    setTimeout(() => {
      req.result = db;
      if (typeof req.onupgradeneeded === "function") {
        req.onupgradeneeded({ target: req });
      }
      req.onsuccess?.();
    }, 0);
    return req;
  },
};

(async () => {
const first = await addQueueItem(900001, "film");
if (!first.ok) throw new Error("add1 failed");
const dup = await addQueueItem(900001, "film");
if (dup.ok || dup.reason !== "duplicate") throw new Error("duplicate not rejected");
const otherType = await addQueueItem(900001, "series");
if (!otherType.ok) throw new Error("other type should be allowed");

const old = {
  id: "old",
  tmdb_id: 900010,
  type: "film",
  added_at: new Date(Date.now() - QUEUE_RETENTION_MS - 1000).toISOString(),
};
const store = dbs.get("mt-follow-queue");
store.tables.get("follow_queue")?.set(old.id, old);
const purged = await purgeExpiredQueue();
if (!purged.length) throw new Error("expired not purged");
if (!isExpired(old)) throw new Error("isExpired should be true");

if (alternateFollowType("film") !== "series" || alternateFollowType("series") !== "film") {
  throw new Error("alternate type failed");
}
})().then(() => process.exit(0)).catch((err) => { console.error(err); process.exit(1); });
"""
    proc = _tsx(script)
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_offline_sync_retries_alternate_type():
    script = r"""
import { syncQueueItem } from "./src/pwa/offlineSync.ts";
import type { QueueItem } from "./src/pwa/offlineQueue.ts";

globalThis.document = { cookie: "" };
const calls = [];
globalThis.fetch = async (url, init) => {
  const body = init?.body ? JSON.parse(String(init.body)) : {};
  calls.push(body.type);
  if (body.type === "film") {
    return { ok: false, status: 404, statusText: "Not Found", json: async () => ({ detail: "TMDB introuvable" }) };
  }
  return {
    ok: true,
    status: 201,
    json: async () => ({ id: 42, title: "Film exemple A" }),
  };
};

const item = {
  id: "x",
  tmdb_id: 900001,
  type: "film",
  added_at: new Date().toISOString(),
};

(async () => {
// minimal indexedDB for update/remove during sync
const dbs = new Map();
class FDBRequest { result; error; onsuccess; onerror; _resolve(v){this.result=v;this.onsuccess?.();} _reject(e){this.error=e;this.onerror?.();} }
class FDBIndex { constructor(store){this.store=store;} get(key){ const r=new FDBRequest(); const hit=[...this.store.data.values()].find((row)=>row.tmdb_id===key[0]&&row.type===key[1]); setTimeout(()=>r._resolve(hit),0); return r; } }
class FDBObjectStore { constructor(tx,name){ this.data=tx.db.tables.get(name)??new Map(); tx.db.tables.set(name,this.data); this.indexes={by_tmdb_type:new FDBIndex(this)}; } index(n){return this.indexes[n];} add(v){const r=new FDBRequest(); this.data.set(v.id,v); setTimeout(()=>r._resolve(v.id),0); return r;} put(v){const r=new FDBRequest(); this.data.set(v.id,v); setTimeout(()=>r._resolve(v.id),0); return r;} delete(k){const r=new FDBRequest(); this.data.delete(k); setTimeout(()=>r._resolve(),0); return r;} get(k){const r=new FDBRequest(); setTimeout(()=>r._resolve(this.data.get(k)),0); return r;} getAll(){const r=new FDBRequest(); setTimeout(()=>r._resolve([...this.data.values()]),0); return r;} }
class FDBTransaction { constructor(db,name){ this.db=db; this.store=new FDBObjectStore(this,name); this.oncomplete=null; setTimeout(()=>this.oncomplete?.(),5);} objectStore(){return this.store;} }
class FDBDatabase { tables=new Map(); objectStoreNames={contains:(n)=>this.tables.has(n)}; close(){} createObjectStore(n){const m=new Map(); this.tables.set(n,m); const s=new FDBObjectStore({db:this,store:m},n); s.createIndex=()=>({}); return s;} transaction(n,m){return new FDBTransaction(this,n);} }
globalThis.indexedDB={ open(name){ const r=new FDBRequest(); const db=dbs.get(name)??new FDBDatabase(); dbs.set(name,db); setTimeout(()=>{r.result=db; if(typeof r.onupgradeneeded==="function"){r.onupgradeneeded({target:r});} r.onsuccess?.();},0); return r; } };
dbs.set("mt-follow-queue", new FDBDatabase());
dbs.get("mt-follow-queue").tables.set("follow_queue", new Map([["x", item]]));

const row = await syncQueueItem(item);
if (row.outcome !== "added" || !row.triedAlternate) {
  throw new Error(JSON.stringify({ calls, row }));
}
if (calls.join(",") !== "film,series") {
  throw new Error("expected film then series");
}
})().then(() => process.exit(0)).catch((err) => { console.error(err); process.exit(1); });
"""
    proc = _tsx(script)
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_clear_offline_user_data_on_logout():
    script = r"""
import { saveDashboardSnapshot } from "./src/pwa/offlineSnapshot.ts";
import { FOLLOW_QUEUE_DB_NAME } from "./src/pwa/offlineQueue.ts";
import { clearOfflineUserData, OFFLINE_STORAGE_PREFIX } from "./src/pwa/clearOfflineUserData.ts";

const storage = new Map();
globalThis.localStorage = {
  get length() { return storage.size; },
  key(i) { return [...storage.keys()][i] ?? null; },
  getItem(k) { return storage.get(k) ?? null; },
  setItem(k, v) { storage.set(k, String(v)); },
  removeItem(k) { storage.delete(k); },
};

class FDBRequest { result; onsuccess; onerror; _ok(v){this.result=v;this.onsuccess?.();} }
const dbs = new Set();
globalThis.indexedDB = {
  deleteDatabase(name) {
    const r = new FDBRequest();
    dbs.delete(name);
    setTimeout(() => r._ok(), 0);
    return r;
  },
};

(async () => {
  saveDashboardSnapshot({ section: "exemple" });
  storage.set("mt.offline.extra.v1", "{}");
  storage.set("mt.sidebarCollapsed", "1");
  dbs.add(FOLLOW_QUEUE_DB_NAME);
  await clearOfflineUserData();
  for (const k of [...storage.keys()]) {
    if (k.startsWith(OFFLINE_STORAGE_PREFIX)) throw new Error(`leftover ${k}`);
  }
  if (!storage.has("mt.sidebarCollapsed")) throw new Error("other keys removed");
  if (dbs.has(FOLLOW_QUEUE_DB_NAME)) throw new Error("idb not cleared");
})().then(() => process.exit(0)).catch((e) => { console.error(e); process.exit(1); });
"""
    proc = _tsx(script)
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_offline_follow_panel_adds_numeric_id_and_tmdb_url():
    script = r"""
import { listQueueItems } from "./src/pwa/offlineQueue.ts";
import { submitOfflineFollowAdd } from "./src/pwa/offlineFollowAdd.ts";

class FDBRequest {
  result = undefined;
  error = null;
  onsuccess = null;
  onerror = null;
  _resolve(result) { this.result = result; this.onsuccess?.(); }
  _reject(err) { this.error = err; this.onerror?.(); }
}

class FDBIndex {
  constructor(store) { this.store = store; }
  get(key) {
    const req = new FDBRequest();
    const hit = [...this.store.data.values()].find((row) => row.tmdb_id === key[0] && row.type === key[1]);
    setTimeout(() => req._resolve(hit ?? undefined), 0);
    return req;
  }
}

class FDBObjectStore {
  constructor(tx, name) {
    this.data = tx.db.tables.get(name) ?? new Map();
    tx.db.tables.set(name, this.data);
    this.indexes = { by_tmdb_type: new FDBIndex(this) };
  }
  index(name) { return this.indexes[name]; }
  add(value) {
    const req = new FDBRequest();
    this.data.set(value.id, value);
    setTimeout(() => req._resolve(value.id), 0);
    return req;
  }
  getAll() {
    const req = new FDBRequest();
    setTimeout(() => req._resolve([...this.data.values()]), 0);
    return req;
  }
}

class FDBTransaction {
  constructor(db, name) {
    this.db = db;
    this.store = new FDBObjectStore(this, name);
    setTimeout(() => this.oncomplete?.(), 5);
  }
  objectStore() { return this.store; }
}

class FDBDatabase {
  tables = new Map();
  objectStoreNames = { contains: (n) => this.tables.has(n) };
  close() {}
  createObjectStore(name) {
    this.tables.set(name, new Map());
    return { createIndex: () => ({}) };
  }
  transaction(name) { return new FDBTransaction(this, name); }
}

const dbs = new Map();
globalThis.indexedDB = {
  open(name, version) {
    const req = new FDBRequest();
    const db = dbs.get(name) ?? new FDBDatabase();
    dbs.set(name, db);
    setTimeout(() => {
      req.result = db;
      if (typeof req.onupgradeneeded === "function") {
        req.onupgradeneeded({ target: req });
      }
      req.onsuccess?.();
    }, 0);
    return req;
  },
};

(async () => {
  const first = await submitOfflineFollowAdd("1234567", "film");
  if (!first.ok) throw new Error(first.reason ?? "add1");
  const second = await submitOfflineFollowAdd("https://wawa.test/tv/7654321", "series");
  if (!second.ok) throw new Error(second.reason ?? "add2");
  const items = await listQueueItems();
  if (items.length !== 2) throw new Error(`count ${items.length}`);
  const byId = new Map(items.map((row) => [row.tmdb_id, row.type]));
  if (byId.get(1234567) !== "film") throw new Error("numeric");
  if (byId.get(7654321) !== "series") throw new Error("url");
})().then(() => process.exit(0)).catch((e) => { console.error(e); process.exit(1); });
"""
    proc = _tsx(script)
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_pwa_build_includes_manifest_icons_and_service_worker():
    if not DIST.is_dir():
        subprocess.run(["npm", "ci"], cwd=FRONT, check=True, timeout=300)
        subprocess.run(["npm", "run", "build"], cwd=FRONT, check=True, timeout=300)
    sw = DIST / "sw.js"
    assert sw.is_file(), "sw.js manquant dans dist"
    for name in ("icon-192.png", "icon-512.png", "icon-512-maskable.png"):
        assert (DIST / name).is_file(), f"{name} manquant"
    index = (DIST / "index.html").read_text(encoding="utf-8")
    assert "manifest.webmanifest" in index
    assert 'theme-color" content="#141618"' in index
