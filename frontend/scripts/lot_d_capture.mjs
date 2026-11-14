import { chromium } from "playwright";
import { mkdir, readFile, rm } from "node:fs/promises";
import { createHash } from "node:crypto";
import { spawn } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "..");
const out = "/opt/cursor/artifacts";
const demoDir = process.env.LOT_D_UI_DEMO_DIR || `/tmp/lot-d-ui-demo-${process.pid}`;
const apiPort = Number(process.env.LOT_D_API_PORT || "8080");
const appUrl = process.env.LOT_D_APP_URL || "http://127.0.0.1:5173";

async function shot(page, name) {
  const file = `${out}/${name}`;
  await page.screenshot({ path: file, fullPage: true });
  const buf = await readFile(file);
  const sha = createHash("sha256").update(buf).digest("hex").slice(0, 16);
  console.log(`saved ${file} sha256:${sha}`);
}

async function waitHttp(url, attempts = 40) {
  for (let i = 0; i < attempts; i += 1) {
    try {
      const res = await fetch(url);
      if (res.ok || res.status < 500) {
        return;
      }
    } catch {
      /* retry */
    }
    await new Promise((r) => setTimeout(r, 500));
  }
  throw new Error(`service not ready: ${url}`);
}

async function login(page) {
  await page.goto(`${appUrl}/login`, { waitUntil: "networkidle" });
  await page.getByLabel(/Nom d.utilisateur/i).fill("admin");
  await page.getByLabel(/Mot de passe/i).fill("test-admin-password");
  await page.getByRole("button", { name: /Connexion|Se connecter/i }).click();
  await page.waitForTimeout(2000);
  if (page.url().includes("/login")) {
    throw new Error(`login failed, still on ${page.url()}`);
  }
}

function runSeed(dir) {
  return new Promise((resolve, reject) => {
    const seed = spawn("python3", [path.join(root, "archives/scripts/seed_lot_d_ui_demo.py")], {
      env: { ...process.env, LOT_D_UI_DEMO_DIR: dir },
      stdio: "inherit",
    });
    seed.on("close", (code) => (code === 0 ? resolve() : reject(new Error(`seed exit ${code}`))));
  });
}

function startUvicorn() {
  return spawn(
    "python3",
    ["-m", "uvicorn", "mediatracker.api.app:app", "--host", "127.0.0.1", "--port", String(apiPort)],
    {
      cwd: root,
      env: {
        ...process.env,
        PYTHONPATH: `${root}/backend:${root}`,
        SECRET_KEY: "lot-d-demo-secret-key-0123456789",
        ADMIN_PASSWORD: "test-admin-password",
        DATABASE_URL: `sqlite:///${demoDir}/app.db`,
        DATA_DIR: `${demoDir}/data`,
        CONFIG_DIR: `${demoDir}/config`,
        DISK_MIN_FREE_GB: "0",
      },
      stdio: "ignore",
    },
  );
}

async function assertReglages(page) {
  const text = await page.innerText("body");
  if (!text.includes("Profils de repli") || !text.includes("Ordre global des profils")) {
    throw new Error("page réglages : section Profils de repli absente");
  }
}

async function assertFiche(page) {
  const text = await page.innerText("body");
  if (!text.includes("Chaîne pour cette fiche") || !text.includes("Profil principal")) {
    throw new Error("fiche : préférences de suivi absentes");
  }
}

await rm(demoDir, { recursive: true, force: true }).catch(() => {});
await runSeed(demoDir);
const meta = JSON.parse(await readFile(`${demoDir}/seed-meta.json`, "utf8"));
const uvicorn = startUvicorn();
const vite = spawn("npm", ["run", "dev", "--", "--host", "127.0.0.1", "--port", "5173", "--strictPort"], {
  cwd: path.join(root, "frontend"),
  stdio: "ignore",
  env: { ...process.env },
});
await waitHttp(`http://127.0.0.1:${apiPort}/healthz`);
await waitHttp(appUrl);
await mkdir(out, { recursive: true });
const browser = await chromium.launch();
try {
  const desk = await browser.newPage({ viewport: { width: 1280, height: 800 } });
  await login(desk);
  await desk.goto(`${appUrl}/reglages/qualite`, { waitUntil: "networkidle" });
  await assertReglages(desk);
  await shot(desk, "lot-d-reglages-desktop1280.png");

  const phone = await browser.newPage({
    viewport: { width: 390, height: 844 },
    isMobile: true,
    hasTouch: true,
  });
  await login(phone);
  await phone.goto(`${appUrl}/reglages/qualite`, { waitUntil: "networkidle" });
  await assertReglages(phone);
  await shot(phone, "lot-d-reglages-phone390.png");

  await desk.goto(`${appUrl}/medias/${meta.media_id}?reglages=ouverts`, { waitUntil: "domcontentloaded", timeout: 60000 });
  await desk.locator("[data-fiche-settings]").waitFor({ state: "visible" });
  async function openFichePrefs(page) {
    await page.locator("[data-fiche-settings]").waitFor({ state: "visible", timeout: 30000 });
    await page.evaluate(() => {
      const block = document.querySelector("[data-fiche-settings]");
      if (block instanceof HTMLDetailsElement) {
        block.open = true;
      }
      const nested = document.querySelector("[data-testid='fiche-prefs-details']");
      if (nested instanceof HTMLDetailsElement) {
        nested.open = true;
      }
    });
    await page.getByText("Chaîne pour cette fiche").waitFor({ state: "visible", timeout: 30000 });
  }

  await openFichePrefs(desk);
  await assertFiche(desk);
  await shot(desk, "lot-d-fiche-desktop1280.png");

  await phone.goto(`${appUrl}/medias/${meta.media_id}?reglages=ouverts`, { waitUntil: "domcontentloaded", timeout: 60000 });
  await openFichePrefs(phone);
  await assertFiche(phone);
  await shot(phone, "lot-d-fiche-phone390.png");

  console.log(`capture ok media_id=${meta.media_id}`);
} finally {
  await browser.close();
  vite.kill("SIGTERM");
  uvicorn.kill("SIGTERM");
}
