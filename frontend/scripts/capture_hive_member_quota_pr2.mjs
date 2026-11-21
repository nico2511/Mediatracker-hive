/**
 * Captures vue membre quota par source (390 + 1440), données fictives.
 */
import { chromium } from "playwright";
import { createHash } from "node:crypto";
import { mkdir, readFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "..");
const out = process.env.PR2_CAPTURE_OUT || path.join(repoRoot, "artifacts", "hive-quota-member-pr2");
const base = process.env.T7_APP_URL || "http://127.0.0.1:5173";
const capturePassword = process.env.PR2_CAPTURE_ADMIN_PASSWORD || process.env.PR433_ADMIN_PASSWORD;
const RUCHE = "7a3e91b2c4d5e6f7a8b9c0d1e2f3a4b5";
const GIB = 1024 ** 3;

const membersFixture = {
  ruche_id_hex: RUCHE,
  is_creator: false,
  weekly_quota_bytes: 100 * GIB,
  quota_week_start: "2026-11-17",
  quota_reset_at: "2026-11-24T00:00:00Z",
  catalog_epoch: 0,
  catalog_reset_at: null,
  members: [
    {
      instance_id: "inst-a",
      display_name: "Instance A",
      pull_only: false,
      is_self: false,
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
      instance_id: "inst-e",
      display_name: "Instance E",
      pull_only: false,
      is_self: true,
      app_version: "1.4.0",
      hive_protocol: 1,
      capabilities: [],
      shares_in_ruche: false,
    },
  ],
};

const myQuotaFixture = {
  ruche_id_hex: RUCHE,
  week_start: "2026-11-17",
  reset_at: "2026-11-24T00:00:00Z",
  sources: [
    {
      status: "ok",
      source_instance_id: "inst-a",
      source_display_name: "Instance A",
      bytes_used: Math.round(99.4 * GIB),
      quota_bytes: 100 * GIB,
      reset_at: "2026-11-24T00:00:00Z",
      receive_quota_status: "exceeded",
    },
    {
      status: "ok",
      source_instance_id: "inst-b",
      source_display_name: "Instance B",
      bytes_used: Math.round(12 * GIB),
      quota_bytes: 100 * GIB,
      reset_at: "2026-11-24T00:00:00Z",
      receive_quota_status: "normal",
    },
  ],
};

async function login(page) {
  if (!capturePassword) {
    throw new Error("Définir PR2_CAPTURE_ADMIN_PASSWORD (ou PR433_ADMIN_PASSWORD).");
  }
  await page.goto(`${base}/login`, { waitUntil: "networkidle" });
  await page.getByLabel(/Nom d.utilisateur/i).fill("admin");
  await page.getByLabel(/Mot de passe/i).fill(capturePassword);
  await page.getByRole("button", { name: /Connexion|Se connecter/i }).click();
  await page.waitForURL((url) => !url.pathname.includes("/login"), { timeout: 15000 });
}

async function shot(page, filePath) {
  await page.screenshot({ path: filePath, fullPage: true });
  const buf = await readFile(filePath);
  const sha = createHash("sha256").update(buf).digest("hex").slice(0, 16);
  console.log(`saved ${filePath} sha256:${sha}`);
}

async function routeMocks(page) {
  await page.route("**/api/v1/setup/status**", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ needs_password: false, completed: true }),
    });
  });
  await page.route("**/api/v1/hive/overview**", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ enabled: true, gossip_interval_sec: 60 }),
    });
  });
  await page.route(`**/api/v1/hive/ruches/${RUCHE}/detail**`, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(membersFixture),
    });
  });
  await page.route(`**/api/v1/hive/ruches/${RUCHE}/my-quota**`, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(myQuotaFixture),
    });
  });
  await page.route(`**/api/v1/hive/join/pending/${RUCHE}**`, async (route) => {
    await route.fulfill({ status: 200, contentType: "application/json", body: "[]" });
  });
}

async function main() {
  await mkdir(out, { recursive: true });
  const browser = await chromium.launch();
  try {
    const desktop = await browser.newPage({ viewport: { width: 1440, height: 900 } });
    await login(desktop);
    await routeMocks(desktop);
    await desktop.goto(`${base}/hive/ruches/${RUCHE}/membres`, { waitUntil: "networkidle" });
    try {
      await desktop.waitForSelector("[data-testid=hive-my-quota-panel]", { timeout: 15000 });
    } catch {
      await shot(desktop, `${out}/debug-desktop.png`);
      throw new Error("hive-my-quota-panel absent");
    }
    await shot(desktop, `${out}/membre-desktop-1440.png`);

    const phone = await browser.newPage({
      viewport: { width: 390, height: 844 },
      isMobile: true,
      hasTouch: true,
    });
    await login(phone);
    await routeMocks(phone);
    await phone.goto(`${base}/hive/ruches/${RUCHE}/membres`, { waitUntil: "networkidle" });
    await phone.waitForSelector("[data-testid=hive-my-quota-panel]", { timeout: 15000 });
    await shot(phone, `${out}/membre-phone-390.png`);
  } finally {
    await browser.close();
  }
}

await main();