/**
 * Captures PR #433 — Membres (quota) et catalogue (3 états bouton).
 * Lance seed + uvicorn + vite sauf si T7_APP_URL est déjà servi.
 */
import { chromium } from "playwright";
import { mkdir, readFile } from "node:fs/promises";
import { createHash } from "node:crypto";
import { spawn } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";

const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "..");
const demoDir = process.env.PR433_DEMO_DIR || `/tmp/pr433-demo-${process.pid}`;
const apiPort = Number(process.env.PR433_API_PORT || "8080");
const base = process.env.T7_APP_URL || "http://127.0.0.1:5173";
const out = "/opt/cursor/artifacts/pr433";
const RUCHE = "7a3e91b2c4d5e6f7a8b9c0d1e2f3a4b5";
const WORK_KEY = "wk-demo-film";

const GIB = 1024 ** 3;

const membersFixture = {
  ruche_id_hex: RUCHE,
  is_creator: true,
  weekly_quota_bytes: 100 * GIB,
  quota_week_start: "2026-11-17",
  quota_reset_at: "2026-11-24T00:00:00Z",
  catalog_epoch: 0,
  catalog_reset_at: null,
  members: [
    {
      instance_id: "inst-self",
      display_name: "Instance A",
      pull_only: false,
      is_self: true,
      app_version: "1.4.0",
      hive_protocol: 1,
      capabilities: [],
      shares_in_ruche: true,
    },
    {
      instance_id: "inst-b",
      display_name: "Instance B",
      pull_only: false,
      is_self: false,
      app_version: "1.4.0",
      hive_protocol: 1,
      capabilities: [],
      shares_in_ruche: true,
    },
    {
      instance_id: "inst-c",
      display_name: "Instance C",
      pull_only: false,
      is_self: false,
      app_version: "1.4.0",
      hive_protocol: 1,
      capabilities: [],
      shares_in_ruche: false,
      receive_bytes_used: Math.round(42.3 * GIB),
      receive_quota_bytes: 100 * GIB,
      receive_quota_status: "normal",
    },
    {
      instance_id: "inst-d",
      display_name: "Instance D",
      pull_only: true,
      is_self: false,
      app_version: "1.4.0",
      hive_protocol: 1,
      capabilities: [],
      shares_in_ruche: false,
      receive_bytes_used: Math.round(82.6 * GIB),
      receive_quota_bytes: 100 * GIB,
      receive_quota_status: "near",
    },
    {
      instance_id: "inst-e",
      display_name: "Instance E",
      pull_only: false,
      is_self: false,
      app_version: "1.4.0",
      hive_protocol: 1,
      capabilities: [],
      shares_in_ruche: false,
      receive_bytes_used: Math.round(99.4 * GIB),
      receive_quota_bytes: 100 * GIB,
      receive_quota_status: "exceeded",
    },
  ],
};

const workFixture = {
  work_key: WORK_KEY,
  title: "Film exemple (2024)",
  type: "movie",
  year: 2024,
  poster_url: null,
  overview: null,
  disponible: true,
  in_library: false,
  season: null,
  episode: null,
  versions: [
    {
      version_key: "1080p+VFF",
      disponible: true,
      source_count: 2,
      quality: "1080p",
      language: "VFF",
      size_bytes: Math.round(4.2 * GIB),
      recommended: true,
      profile_compatible: true,
    },
    {
      version_key: "720p+VFF",
      disponible: true,
      source_count: 1,
      quality: "720p",
      language: "VFF",
      size_bytes: Math.round(2.1 * GIB),
      recommended: false,
      profile_compatible: true,
    },
    {
      version_key: "2160p+VOSTFR",
      disponible: true,
      source_count: 1,
      quality: "2160p",
      language: "VOSTFR",
      size_bytes: Math.round(12.8 * GIB),
      recommended: false,
      profile_compatible: true,
    },
  ],
  media_id: null,
  media_item_id: null,
  episode_count: null,
  episodes: null,
  followed: false,
  shared_by_me: false,
  on_disk: false,
  card_state: "disponible",
  progress_pct: null,
};

async function login(page) {
  await page.goto(`${base}/login`, { waitUntil: "networkidle" });
  await page.getByLabel(/Nom d.utilisateur/i).fill("admin");
  const capturePassword =
    process.env.PR433_ADMIN_PASSWORD ||
    (process.env.T7_APP_URL ? "test-admin-password" : "Choix-Solide-42!");
  await page.getByLabel(/Mot de passe/i).fill(capturePassword);
  await page.getByRole("button", { name: /Connexion|Se connecter/i }).click();
  await page.waitForURL((url) => !url.pathname.includes("/login"), { timeout: 15000 });
}

async function shot(page, path) {
  await page.screenshot({ path, fullPage: true });
  const buf = await readFile(path);
  const sha = createHash("sha256").update(buf).digest("hex").slice(0, 16);
  console.log(`saved ${path} sha256:${sha}`);
}

async function routeHiveMocks(page) {
  await page.route("**/api/v1/hive/overview**", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ enabled: true, gossip_interval_sec: 60 }),
    });
  });
  for (const tail of ["/mine", "/received", "/wanted"]) {
    await page.route(`**/api/v1/hive/requests${tail}**`, async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ items: [] }),
      });
    });
  }
  await page.route(`**/api/v1/hive/ruches/${RUCHE}/detail**`, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(membersFixture),
    });
  });
  await page.route(`**/api/v1/hive/join/pending/${RUCHE}**`, async (route) => {
    await route.fulfill({ status: 200, contentType: "application/json", body: "[]" });
  });
  await page.route(`**/api/v1/hive/catalogue/${WORK_KEY}**`, async (route) => {
    if (route.request().method() === "GET") {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(workFixture),
      });
      return;
    }
    await route.continue();
  });
  await page.route(`**/api/v1/hive/catalogue/${WORK_KEY}/request**`, async (route) => {
    const raw = route.request().postData() || "";
    let versionKey = "";
    try {
      versionKey = JSON.parse(raw).version_key || "";
    } catch {
      versionKey = "";
    }
    if (versionKey === "1080p+VFF") {
      await new Promise((r) => setTimeout(r, 120_000));
      return;
    }
    if (versionKey === "720p+VFF") {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ queued: true, state: "pending" }),
      });
      return;
    }
    if (versionKey === "2160p+VOSTFR") {
      await route.fulfill({
        status: 422,
        contentType: "application/json",
        body: JSON.stringify({
          title: "Requête invalide",
          detail: "Transfert indisponible.",
        }),
      });
      return;
    }
    await route.continue();
  });
}

async function captureMembers(page, tag) {
  await routeHiveMocks(page);
  await page.goto(`${base}/hive/ruches/${RUCHE}/membres`, { waitUntil: "networkidle" });
  await page.waitForSelector("[data-testid=hive-quota-ruche-panel]", { timeout: 15000 });
  await page.waitForSelector("[data-testid=hive-member-quota-status]", { timeout: 15000 });
  await shot(page, `${out}/membres-quota-${tag}.png`);
}

async function captureCatalogueComposite(page, tag) {
  await routeHiveMocks(page);
  await page.goto(`${base}/hive/catalogue/${WORK_KEY}`, { waitUntil: "networkidle" });
  const list = page.getByTestId("hive-catalogue-version-list");
  await list.waitFor({ timeout: 15000 });

  await page.getByTestId("hive-version-download-720p+VFF").click();
  await page.getByTestId("hive-version-requested").waitFor({ timeout: 10000 });

  await page.getByTestId("hive-version-download-2160p+VOSTFR").click();
  await page.getByTestId("hive-version-error-2160p+VOSTFR").waitFor({ timeout: 10000 });

  await page.getByTestId("hive-version-download-1080p+VFF").click();
  await page.getByRole("button", { name: "Envoi…" }).first().waitFor({ timeout: 5000 });

  const outPath = `${out}/catalogue-bouton-etats-${tag}.png`;
  await list.screenshot({ path: outPath });
  const buf = await readFile(outPath);
  const sha = createHash("sha256").update(buf).digest("hex").slice(0, 16);
  console.log(`saved ${outPath} sha256:${sha}`);
}

function runSeed() {
  return new Promise((resolve, reject) => {
    const seed = spawn(
      "python3",
      [path.join(repoRoot, "scripts/seed_pr433_ui.py")],
      {
        env: { ...process.env, PR433_DEMO_DIR: demoDir },
        stdio: "inherit",
      },
    );
    seed.on("close", (code) => (code === 0 ? resolve() : reject(new Error(`seed exit ${code}`))));
  });
}

function startUvicorn() {
  return spawn(
    "python3",
    ["-m", "uvicorn", "mediatracker.api.app:app", "--host", "127.0.0.1", "--port", String(apiPort)],
    {
      cwd: repoRoot,
      env: {
        ...process.env,
        PYTHONPATH: `${repoRoot}/backend:${repoRoot}`,
        SECRET_KEY: "pr433-demo-secret-key-012345",
        ADMIN_PASSWORD: "Choix-Solide-42!",
        DATABASE_URL: `sqlite:///${demoDir}/app.db`,
        DATA_DIR: `${demoDir}/data`,
        CONFIG_DIR: `${demoDir}/config`,
        DISK_MIN_FREE_GB: "0",
      },
      stdio: "ignore",
    },
  );
}

function startDev() {
  return spawn("npm", ["run", "dev", "--", "--host", "127.0.0.1", "--port", "5173"], {
    cwd: path.join(repoRoot, "frontend"),
    env: {
      ...process.env,
      VITE_DEV_PROXY_TARGET: `http://127.0.0.1:${apiPort}`,
    },
    stdio: "ignore",
  });
}

async function main() {
  const selfHosted = !process.env.T7_APP_URL;
  let uvicorn;
  let dev;
  if (selfHosted) {
    await runSeed();
    uvicorn = startUvicorn();
    dev = startDev();
    await new Promise((r) => setTimeout(r, 6000));
  }

  await mkdir(out, { recursive: true });
  const browser = await chromium.launch();

  try {
    const desktop = await browser.newPage({ viewport: { width: 1440, height: 900 } });
    await login(desktop);
    await captureMembers(desktop, "1440");
    await captureCatalogueComposite(desktop, "1440");

    const phone = await browser.newPage({
      viewport: { width: 390, height: 844 },
      isMobile: true,
      hasTouch: true,
    });
    await login(phone);
    await captureMembers(phone, "390");
    await captureCatalogueComposite(phone, "390");
  } finally {
    await browser.close();
    if (uvicorn) {
      uvicorn.kill("SIGTERM");
    }
    if (dev) {
      dev.kill("SIGTERM");
    }
  }
  console.log("capture pr433 ok");
}

await main();