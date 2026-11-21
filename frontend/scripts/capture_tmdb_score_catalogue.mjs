import { chromium } from "playwright";
import { mkdir, readFile } from "node:fs/promises";
import { spawn } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "..");
const outDir = path.resolve(process.env.TMDB_SCORE_CAPTURE_OUT || path.join(root, "captures-tmdb-score"));
const demoDir = process.env.TMDB_SCORE_DEMO_DIR || `/tmp/tmdb-score-demo-${process.pid}`;
const apiPort = Number(process.env.TMDB_SCORE_API_PORT || "8091");
const appUrl = process.env.TMDB_SCORE_APP_URL || `http://127.0.0.1:${process.env.TMDB_SCORE_VITE_PORT || "5174"}`;

function requireEnv(name) {
  const value = process.env[name];
  if (!value) {
    throw new Error(`Variable d'environnement requise : ${name}`);
  }
  return value;
}

const secretKey = requireEnv("TMDB_SCORE_SECRET_KEY");
const adminPassword = requireEnv("TMDB_SCORE_ADMIN_PASSWORD");

const childEnv = {
  ...process.env,
  TMDB_SCORE_DEMO_DIR: demoDir,
  TMDB_SCORE_SECRET_KEY: secretKey,
  TMDB_SCORE_ADMIN_PASSWORD: adminPassword,
};

async function shot(page, name) {
  await mkdir(outDir, { recursive: true });
  const file = path.join(outDir, name);
  await page.screenshot({ path: file, fullPage: false });
  console.log(`saved ${file}`);
}

function runSeed() {
  return new Promise((resolve, reject) => {
    const seed = spawn("python3", [path.join(root, "scripts/seed_tmdb_score_ui_demo.py")], {
      env: childEnv,
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
        ...childEnv,
        PYTHONPATH: `${root}/backend:${root}`,
        SECRET_KEY: secretKey,
        ADMIN_USERNAME: "admin",
        ADMIN_PASSWORD: adminPassword,
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
  const vitePort = new URL(appUrl).port || "5174";
  return spawn("npm", ["run", "dev", "--", "--host", "127.0.0.1", "--port", vitePort], {
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
  await page.locator('input[autocomplete="username"]').fill("admin");
  await page.locator('input[autocomplete="current-password"]').fill(adminPassword);
  await page.getByRole("button", { name: "Se connecter" }).click();
  try {
    await page.waitForURL((url) => !url.pathname.startsWith("/login"), { timeout: 20_000 });
  } catch {
    const errText = (await page.locator(".error").textContent().catch(() => "")) || "";
    throw new Error(`Connexion échouée (resté sur /login). ${errText}`.trim());
  }
  if (new URL(page.url()).pathname.startsWith("/login")) {
    throw new Error("Connexion échouée : encore sur /login après redirection attendue.");
  }
}

async function waitProofSelector(page, selector, label) {
  const loc = page.locator(selector).first();
  await loc.scrollIntoViewIfNeeded({ timeout: 30_000 }).catch(() => undefined);
  await loc.waitFor({ state: "visible", timeout: 30_000 });
  const text = (await loc.textContent())?.trim() ?? "";
  if (!text.includes("★") && !/TMDB\s+\d/.test(text)) {
    throw new Error(`Élément de preuve invalide pour ${label} (${selector}) : « ${text} »`);
  }
  return loc;
}

async function captureRoute(page, route, basename, width, proofSelector, scrollTo) {
  await page.setViewportSize({ width, height: width === 390 ? 844 : 900 });
  await page.goto(`${appUrl}${route}`, { waitUntil: "domcontentloaded", timeout: 60_000 });
  if (scrollTo) {
    await page.locator(scrollTo).scrollIntoViewIfNeeded({ timeout: 30_000 });
  }
  const proofLoc = await waitProofSelector(page, proofSelector, `${basename} @ ${width}`);
  await proofLoc.evaluate((el) => el.scrollIntoView({ block: "center", inline: "nearest" }));
  await shot(page, `${basename}-${width === 390 ? "telephone-390" : "ordinateur-1440"}.png`);
}

async function waitForStack() {
  const deadline = Date.now() + 60_000;
  while (Date.now() < deadline) {
    try {
      const response = await fetch(`${appUrl}/healthz`);
      if (response.ok) {
        return;
      }
    } catch {
      // Vite ou l’API pas encore prêts
    }
    await new Promise((r) => setTimeout(r, 500));
  }
  throw new Error(`Stack inaccessible (${appUrl}/healthz)`);
}

async function main() {
  await runSeed();
  const meta = JSON.parse(await readFile(`${demoDir}/seed-meta.json`, "utf8"));
  const uvicorn = startUvicorn();
  const dev = startDev();
  await waitForStack();
  const browser = await chromium.launch();
  try {
    const page = await browser.newPage();
    await login(page);
    const routes = [
      { path: "/medias", name: "score-cartes", proof: ".media-library-grid .tmdb-rating-card-line" },
      { path: `/medias/${meta.media_fiche_id}`, name: "score-fiche", proof: ".fiche-hero-tmdb-rating" },
    ];
    for (const width of [390, 1440]) {
      for (const { path: route, name, proof, scrollTo } of routes) {
        await captureRoute(page, route, name, width, proof, scrollTo);
      }
    }
    await page.close();
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