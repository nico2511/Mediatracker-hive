import { chromium } from "playwright";
import { execSync } from "node:child_process";
import { createHash } from "node:crypto";
import { access, mkdir, readFile } from "node:fs/promises";
import { spawn, spawnSync } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "..");
const frontend = path.join(root, "frontend");
const out = "/opt/cursor/artifacts";
const apiPort = 8080;
const appPort = 52300 + (process.pid % 700);
const appUrl = `http://127.0.0.1:${appPort}`;
const RUN_SHA = execSync("git rev-parse HEAD", { cwd: root, encoding: "utf8" }).trim();
const RUN_START_ISO = new Date().toISOString();
/** @type {import("node:child_process").ChildProcess | null} */
let demoApiChild = null;

function log(msg) {
  console.log(`[${new Date().toISOString()}] ${msg}`);
}

async function sha(file) {
  const buf = await readFile(file);
  return createHash("sha256").update(buf).digest("hex").slice(0, 16);
}

async function shotDialog(page, name) {
  const file = `${out}/${name}`;
  await page.locator(".dialog-backdrop .dialog.panel").screenshot({ path: file });
  log(`ASSERT_DIALOG saved ${file} sha256:${await sha(file)}`);
}

async function shotPage(page, name) {
  const file = `${out}/${name}`;
  await page.screenshot({ path: file, fullPage: true });
  log(`ASSERT_PAGE saved ${file} sha256:${await sha(file)}`);
}

function startProc(cmd, args, env = {}, cwd = root) {
  return spawn(cmd, args, {
    cwd,
    env: { ...process.env, ...env },
    stdio: "inherit",
  });
}

async function waitUrl(url, ok = (s) => s < 500, ms = 90000) {
  const start = Date.now();
  while (Date.now() - start < ms) {
    try {
      const r = await fetch(url);
      if (ok(r.status)) return;
    } catch {
      /* retry */
    }
    await new Promise((r) => setTimeout(r, 400));
  }
  throw new Error(`timeout ${url}`);
}

async function runSeed(demoDir, withDiskFiles) {
  const args = [
    path.join(root, "archives/scripts/seed_tmdb_dialog_demo.py"),
    "--data-dir",
    demoDir,
    "--config-dir",
    path.join(demoDir, "config"),
  ];
  if (withDiskFiles) args.push("--with-disk-files");

  return new Promise((resolve, reject) => {
    const child = spawn("python3", args, {
      cwd: root,
      env: {
        ...process.env,
        PYTHONPATH: `${root}/backend:${root}`,
        SECRET_KEY: "tmdb-dialog-demo-key-012345678901",
        ADMIN_PASSWORD: "test-admin-password",
      },
      stdio: ["ignore", "pipe", "inherit"],
    });
    let outBuf = "";
    child.stdout.on("data", (c) => {
      outBuf += c;
    });
    child.on("close", (code) => {
      if (code !== 0) reject(new Error(`seed exit ${code}`));
      else {
        const line = outBuf.trim().split("\n").pop().trim();
        const [mediaId, orphanPath] = line.split("\t");
        resolve({ mediaId: Number(mediaId), orphanPath: orphanPath || null });
      }
    });
  });
}

function portListenerPid(port) {
  const out = spawnSync("sh", ["-c", `fuser -n ${port}/tcp 2>/dev/null || true`], { encoding: "utf8" });
  const line = out.stdout.trim();
  if (!line) {
    return null;
  }
  const pid = Number.parseInt(line.split(/\s+/).pop() ?? "", 10);
  return Number.isFinite(pid) ? pid : null;
}

async function assertPortAvailableOrOwned(port) {
  const pid = portListenerPid(port);
  if (pid === null) {
    return;
  }
  if (demoApiChild?.pid === pid) {
    return;
  }
  throw new Error(
    `Port ${port} occupé par PID ${pid} (hors recette). Arrêtez ce processus avant de relancer.`,
  );
}

async function stopApi(api) {
  if (!api || api.exitCode !== null) {
    if (api === demoApiChild) {
      demoApiChild = null;
    }
    return;
  }
  await new Promise((resolve) => {
    const timer = setTimeout(() => {
      try {
        api.kill("SIGKILL");
      } catch {
        /* ignore */
      }
      resolve();
    }, 4000);
    api.once("exit", () => {
      clearTimeout(timer);
      resolve();
    });
    api.kill("SIGTERM");
  });
  if (api === demoApiChild) {
    demoApiChild = null;
  }
}

async function startApi(demoDir) {
  await stopApi(demoApiChild);
  await assertPortAvailableOrOwned(apiPort);
  log(`API_START port=${apiPort} demoDir=${demoDir}`);
  const api = startProc("python3", [path.join(root, "archives/scripts/uvicorn_tmdb_dialog_demo.py")], demoEnv(demoDir));
  demoApiChild = api;
  await waitUrl(`http://127.0.0.1:${apiPort}/healthz`, (s) => s === 200, 120000);
  log(`API_READY port=${apiPort} pid=${api.pid ?? "?"}`);
  return api;
}

function demoEnv(demoDir) {
  return {
    PYTHONPATH: `${root}/backend:${root}`,
    SECRET_KEY: "tmdb-dialog-demo-key-012345678901",
    ADMIN_PASSWORD: "test-admin-password",
    DATABASE_URL: `sqlite:///${demoDir}/app.db`,
    DATA_DIR: demoDir,
    CONFIG_DIR: path.join(demoDir, "config"),
    MTR_DEMO_API_PORT: String(apiPort),
  };
}

async function login(page) {
  await page.goto(`${appUrl}/login`);
  await page.getByLabel(/Nom d.utilisateur/i).fill("admin");
  await page.getByLabel(/Mot de passe/i).fill("test-admin-password");
  await page.getByRole("button", { name: /Connexion|Se connecter/i }).click();
  await page.waitForTimeout(800);
}

async function openIdentificationForm(page, mediaId) {
  await page.goto(`${appUrl}/medias/${mediaId}?reglages=ouverts`);
  const gotMedia = await page.waitForResponse(
    (r) => r.url().includes(`/api/v1/media/${mediaId}`) && r.request().method() === "GET" && r.status() === 200,
    { timeout: 45000 },
  );
  log(`ASSERT_GET_MEDIA status=${gotMedia.status()}`);
  const settings = page.locator("details[data-fiche-settings]");
  await settings.scrollIntoViewIfNeeded();
  const settingsOpen = await settings.evaluate((el) => /** @type {HTMLDetailsElement} */ (el).open);
  if (!settingsOpen) {
    await settings.locator("summary.fold-head").click();
  }
  await settings.locator(".fold-body").waitFor({ state: "visible", timeout: 15000 });
  const identSection = page
    .locator("details.fiche-setting-details")
    .filter({ has: page.locator("summary", { hasText: "Corriger l'identifiant" }) });
  await identSection.scrollIntoViewIfNeeded();
  await identSection.locator("summary").click();
  await identSection.locator("form").waitFor({ state: "visible", timeout: 15000 });
  return identSection;
}

async function submitNewTmdbId(identSection) {
  const tmdbInput = identSection.locator("input[inputmode='numeric']");
  await tmdbInput.fill("200");
  const previewPromise = identSection
    .page()
    .waitForResponse(
      (r) =>
        r.request().method() === "PATCH" &&
        r.url().includes("/api/v1/media/") &&
        r.status() === 409,
      { timeout: 30000 },
    );
  await identSection.getByRole("button", { name: /Corriger et pousser vers Jellyfin/i }).click();
  const preview = await previewPromise;
  const body = await preview.json().catch(() => ({}));
  log(`ASSERT_PATCH_409 status=${preview.status()} needs_confirm=${body.needs_confirm === true}`);
  await identSection.page().locator("#tmdb-id-change-title").waitFor({ timeout: 20000 });
}

function flattenEpisodeSlots(itemsBody) {
  const rows = [];
  for (const season of itemsBody?.seasons ?? []) {
    const sn = season.number;
    for (const ep of season.episodes ?? []) {
      rows.push({ season: sn, episode: ep.episode });
    }
  }
  return rows;
}

async function readBackendState(page, mediaId) {
  return page.evaluate(async (mid) => {
    const [mediaR, itemsR] = await Promise.all([
      fetch(`/api/v1/media/${mid}`),
      fetch(`/api/v1/media/${mid}/items`),
    ]);
    return {
      media: await mediaR.json(),
      itemsBody: await itemsR.json(),
      mediaStatus: mediaR.status,
      itemsStatus: itemsR.status,
    };
  }, mediaId);
}

async function runAnnulerCapture(browser) {
  const demoDir = `/tmp/tmdb-recette-annuler-${process.pid}`;
  await mkdir(demoDir, { recursive: true });
  const { mediaId } = await runSeed(demoDir, false);
  log(`SCENARIO annuler seed mediaId=${mediaId} demoDir=${demoDir}`);

  const api = await startApi(demoDir);

  for (const [tag, width, height] of [
    ["integrated-phone390", 390, 844],
    ["integrated-desktop1280", 1280, 800],
  ]) {
    const ctx = await browser.newContext({ viewport: { width, height } });
    const page = await ctx.newPage();
    let confirmPatchCount = 0;
    page.on("request", (req) => {
      if (req.method() !== "PATCH" || !req.url().includes(`/api/v1/media/${mediaId}`)) return;
      const body = req.postData() ?? "";
      if (body.includes("tmdb_id_confirm")) confirmPatchCount += 1;
    });

    await login(page);
    const identSection = await openIdentificationForm(page, mediaId);
    await submitNewTmdbId(identSection);
    await shotPage(page, `fiche-dialogue-tmdb-${tag}.png`);
    await shotDialog(page, `dialogue-tmdb-${tag}.png`);

    if (tag.includes("phone")) {
      await page.getByRole("button", { name: "Annuler" }).click();
      await page.locator("#tmdb-id-change-title").waitFor({ state: "hidden", timeout: 5000 });
      if (confirmPatchCount !== 0) {
        throw new Error(`ASSERT_ANNULER confirm PATCH count=${confirmPatchCount}`);
      }
      log("ASSERT_ANNULER confirm_patch_count=0");
      const state = await readBackendState(page, mediaId);
      if (state.media.tmdb_id !== 100) {
        throw new Error(`ASSERT_ANNULER tmdb_id=${state.media.tmdb_id} expected 100`);
      }
      log("ASSERT_ANNULER backend_tmdb_id=100 unchanged");
    }
    await ctx.close();
  }

  await stopApi(api);
  return mediaId;
}

async function runConserver(browser) {
  const demoDir = `/tmp/tmdb-recette-conserver-${process.pid}`;
  await mkdir(demoDir, { recursive: true });
  const { mediaId } = await runSeed(demoDir, false);
  log(`SCENARIO conserver seed mediaId=${mediaId}`);

  const api = await startApi(demoDir);

  const ctx = await browser.newContext({ viewport: { width: 1280, height: 800 } });
  const page = await ctx.newPage();
  let confirms = [];
  page.on("request", (req) => {
    if (req.method() === "PATCH" && req.url().includes(`/api/v1/media/${mediaId}`)) {
      confirms.push({ confirm: (req.postData() ?? "").includes("keep_episodes"), body: req.postData() ?? "" });
    }
  });

  await login(page);
  const identSection = await openIdentificationForm(page, mediaId);
  await submitNewTmdbId(identSection);
  const confirmPromise = page.waitForResponse(
    (r) => r.request().method() === "PATCH" && r.url().includes(`/api/v1/media/${mediaId}`) && r.status() === 200,
    { timeout: 30000 },
  );
  await page.getByRole("button", { name: "Conserver les épisodes" }).click();
  const ok = await confirmPromise;
  log(`ASSERT_CONSERVER patch_status=${ok.status()}`);
  const state = await readBackendState(page, mediaId);
  if (state.media.tmdb_id !== 200) throw new Error(`conserver tmdb_id=${state.media.tmdb_id}`);
  const slots = flattenEpisodeSlots(state.itemsBody).map((i) => `${i.season}:${i.episode}`);
  if (slots.length !== 3 || !slots.includes("2:9")) {
    throw new Error(`ASSERT_CONSERVER items=${slots.join(",")}`);
  }
  log("ASSERT_CONSERVER items_count=3 includes_S02E09");
  if (!confirms.some((c) => c.body.includes("keep_episodes"))) {
    throw new Error("ASSERT_CONSERVER missing keep_episodes in PATCH");
  }
  log("ASSERT_CONSERVER tmdb_id_confirm=keep_episodes");

  await ctx.close();
  await stopApi(api);
}

async function runReconstruire(browser) {
  const demoDir = `/tmp/tmdb-recette-rebuild-${process.pid}`;
  await mkdir(demoDir, { recursive: true });
  const { mediaId, orphanPath } = await runSeed(demoDir, true);
  log(`SCENARIO reconstruire seed mediaId=${mediaId} orphan=${orphanPath}`);

  const api = await startApi(demoDir);

  const ctx = await browser.newContext({ viewport: { width: 1280, height: 800 } });
  const page = await ctx.newPage();

  await login(page);
  const identSection = await openIdentificationForm(page, mediaId);
  await submitNewTmdbId(identSection);
  const confirmPromise = page.waitForResponse(
    (r) => r.request().method() === "PATCH" && r.url().includes(`/api/v1/media/${mediaId}`) && r.status() === 200,
    { timeout: 30000 },
  );
  await page.getByRole("button", { name: "Reconstruire depuis TMDB" }).click();
  const ok = await confirmPromise;
  const payload = await ok.json().catch(() => ({}));
  log(`ASSERT_REBUILD patch_status=${ok.status()} tmdb_id=${payload.tmdb_id}`);

  const state = await readBackendState(page, mediaId);
  const slots = new Set(flattenEpisodeSlots(state.itemsBody).map((i) => `${i.season}:${i.episode}`));
  if (slots.has("2:9")) throw new Error("ASSERT_REBUILD S02E09 still present");
  if (!slots.has("1:1") || !slots.has("1:2")) throw new Error(`ASSERT_REBUILD slots=${[...slots]}`);
  log(`ASSERT_REBUILD items=${[...slots].sort().join(",")} (S02E09 absent)`);

  await access(orphanPath);
  log(`ASSERT_REBUILD orphan_file_still_on_disk path=${orphanPath}`);

  await ctx.close();
  await stopApi(api);
}

async function main() {
  await mkdir(out, { recursive: true });
  await assertPortAvailableOrOwned(apiPort);

  log(`RUN_SHA=${RUN_SHA}`);
  log(`RUN_START_ISO=${RUN_START_ISO}`);
  log("STACK_DEMO backend=uvicorn_tmdb_dialog_demo.py (TMDB fetch simulé serveur, données fictives)");
  log(`STACK_DEMO frontend=vite:${appPort} proxy_/api->${apiPort}`);
  log("STACK_DEMO playwright=aucun mock réseau métier PATCH/GET");

  const web = startProc(
    "npm",
    ["run", "dev", "--", "--host", "127.0.0.1", "--port", String(appPort), "--strictPort"],
    {},
    frontend,
  );

  let viteFailed = false;
  web.on("exit", (code) => {
    if (code !== 0 && code !== null) {
      viteFailed = true;
      log(`VITE_EXIT early code=${code}`);
    }
  });

  try {
    await waitUrl(`${appUrl}/`);
    if (viteFailed) throw new Error("Vite a quitté avant la recette (port occupé ou crash)");
    log(`ASSERT_VITE_READY url=${appUrl}`);

    const browser = await chromium.launch();
    await runAnnulerCapture(browser);
    await runConserver(browser);
    await runReconstruire(browser);
    await browser.close();

    const end = new Date().toISOString();
    log(`RUN_END_ISO=${end}`);
    log(`RECETTE_EXIT=0`);
    log(`RUN_SHA=${RUN_SHA}`);
  } finally {
  await stopApi(demoApiChild);
    web.kill("SIGTERM");
  }
}

main().catch((err) => {
  log(`RECETTE_EXIT=1 error=${err?.message ?? err}`);
  log(`RUN_SHA=${RUN_SHA}`);
  console.error(err);
  process.exit(1);
});
