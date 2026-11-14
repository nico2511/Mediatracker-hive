import { chromium } from "playwright";
import { mkdir, readFile } from "node:fs/promises";
import { createHash } from "node:crypto";
import { spawn } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "..");
const out = "/opt/cursor/artifacts";
const demoDir = process.env.HIVE_FICHE_DEMO_DIR || `/tmp/hive-fiche-demo-${process.pid}`;
const apiPort = Number(process.env.HIVE_FICHE_API_PORT || "8080");
const appUrl = process.env.HIVE_FICHE_APP_URL || "http://127.0.0.1:5173";

async function shot(page, name) {
  await mkdir(out, { recursive: true });
  const file = `${out}/${name}`;
  await page.screenshot({ path: file, fullPage: false });
  const buf = await readFile(file);
  const sha = createHash("sha256").update(buf).digest("hex").slice(0, 16);
  console.log(`ASSERT_UI saved ${file} sha256:${sha}`);
}

function runSeed() {
  return new Promise((resolve, reject) => {
    const seed = spawn("python3", [path.join(root, "scripts/seed_t8_ui_demo.py")], {
      env: { ...process.env, T8_UI_DEMO_DIR: demoDir },
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
        SECRET_KEY: "t8-demo-secret-key-012345678901",
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

function startDev() {
  return spawn("npm", ["run", "dev", "--", "--host", "127.0.0.1", "--port", "5173"], {
    cwd: path.join(root, "frontend"),
    env: {
      ...process.env,
      VITE_DEV_PROXY_TARGET: `http://127.0.0.1:${apiPort}`,
    },
    stdio: "ignore",
  });
}

async function login(page) {
  await page.goto(`${appUrl}/login`, { waitUntil: "networkidle" });
  await page.getByLabel(/Nom d.utilisateur/i).fill("admin");
  await page.getByLabel(/Mot de passe/i).fill("test-admin-password");
  await page.getByRole("button", { name: /Connexion|Se connecter/i }).click();
  await page.waitForTimeout(800);
}

async function main() {
  await runSeed();
  const meta = JSON.parse(await readFile(`${demoDir}/seed-meta.json`, "utf8"));
  const mediaId = meta.media_id_share_film;
  const uvicorn = startUvicorn();
  const dev = startDev();
  await new Promise((r) => setTimeout(r, 5000));
  const browser = await chromium.launch();
  try {
    for (const width of [390, 1440]) {
      const page = await browser.newPage({ viewport: { width, height: 900 } });
      await login(page);
      await page.goto(`${appUrl}/medias/${mediaId}`, { waitUntil: "domcontentloaded", timeout: 60_000 });
      await page.waitForTimeout(1500);
      await page
        .locator(".hive-fiche-share-main, .film-file-panel, .fiche-hero")
        .first()
        .waitFor({ state: "visible", timeout: 30_000 })
        .catch(() => undefined);
      await shot(page, `hive-fiche-share-${width}-apres.png`);
      await page.close();
    }
  } finally {
    await browser.close();
    uvicorn.kill("SIGTERM");
    dev.kill("SIGTERM");
  }
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
