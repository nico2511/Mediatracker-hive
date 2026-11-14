import { chromium } from "playwright";
import { mkdir, readFile, rm } from "node:fs/promises";
import { createHash } from "node:crypto";
import { spawn } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "..");
const out = "/opt/cursor/artifacts";
const demoDir = process.env.T8_UI_DEMO_DIR || `/tmp/t8-ui-demo-${process.pid}`;
const apiPort = Number(process.env.T8_API_PORT || "8080");
const appUrl = process.env.T8_APP_URL || "http://127.0.0.1:5173";

async function shot(page, name) {
  const file = `${out}/${name}`;
  await page.screenshot({ path: file, fullPage: true });
  const buf = await readFile(file);
  const sha = createHash("sha256").update(buf).digest("hex").slice(0, 16);
  console.log(`ASSERT_UI saved ${file} sha256:${sha}`);
}

async function login(page) {
  await page.goto(`${appUrl}/login`, { waitUntil: "networkidle" });
  await page.getByLabel(/Nom d.utilisateur/i).fill("admin");
  await page.getByLabel(/Mot de passe/i).fill("test-admin-password");
  await page.getByRole("button", { name: /Connexion|Se connecter/i }).click();
  await page.waitForTimeout(1200);
}

function runSeed(dir, extraEnv = {}) {
  return new Promise((resolve, reject) => {
    const seed = spawn("python3", [path.join(root, "scripts/seed_t8_ui_demo.py")], {
      env: { ...process.env, T8_UI_DEMO_DIR: dir, ...extraEnv },
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

async function assertPickerChrome(page, { minRuches = 1, expectScroll = false }) {
  const ui = await page.evaluate(() => {
    const backdrop = document.querySelector(".hive-picker-backdrop");
    const dialog = document.querySelector(".hive-picker[role='dialog']");
    const apply = document.querySelector(".hive-picker-actions .btn.primary");
    const cancel = document.querySelector(".hive-picker-actions button.btn:not(.primary)");
    const scroll = document.querySelector(".hive-picker-scroll");
    if (!backdrop || !dialog || !apply || !cancel || !scroll) {
      return { ok: false, reason: "missing_nodes" };
    }
    const z = Number(getComputedStyle(backdrop).zIndex);
    const backdropBg = getComputedStyle(backdrop).backgroundColor;
    const dialogBg = getComputedStyle(dialog).backgroundColor;
    const overflow = getComputedStyle(scroll).overflowY;
    const applyRect = apply.getBoundingClientRect();
    const cancelRect = cancel.getBoundingClientRect();
    const vh = window.innerHeight;
    const list = scroll.querySelector("ul");
    return {
      ok: true,
      zIndex: z,
      backdropBg,
      dialogBg,
      overflow,
      applyInViewport: applyRect.top >= 0 && applyRect.bottom <= vh + 1,
      cancelInViewport: cancelRect.top >= 0 && cancelRect.bottom <= vh + 1,
      rucheCount: list ? list.children.length : 0,
      scrollable: scroll.scrollHeight > scroll.clientHeight + 2,
    };
  });
  if (!ui?.ok) throw new Error(`ASSERT picker chrome: ${ui?.reason || "unknown"}`);
  if (ui.zIndex < 2100) throw new Error(`ASSERT picker z-index too low: ${ui.zIndex}`);
  if (!ui.dialogBg || ui.dialogBg === "rgba(0, 0, 0, 0)") {
    throw new Error(`ASSERT picker dialog background transparent: ${ui.dialogBg}`);
  }
  if (!ui.backdropBg.includes("0.55") && !ui.backdropBg.includes("0.5")) {
    throw new Error(`ASSERT picker backdrop not dimmed: ${ui.backdropBg}`);
  }
  if (ui.overflow !== "auto" && ui.overflow !== "scroll") {
    throw new Error(`ASSERT picker list not scrollable overflow: ${ui.overflow}`);
  }
  if (!ui.applyInViewport || !ui.cancelInViewport) {
    throw new Error("ASSERT picker footer buttons outside viewport");
  }
  if (ui.rucheCount < minRuches) {
    throw new Error(`ASSERT picker ruche count ${ui.rucheCount} < ${minRuches}`);
  }
  if (expectScroll && !ui.scrollable) {
    throw new Error("ASSERT picker list should scroll for long ruche list");
  }
  console.log(`ASSERT_UI picker_chrome z=${ui.zIndex} ruches=${ui.rucheCount} scroll=${ui.scrollable}`);
  return ui;
}

async function openPickerOnShareFilm(page, meta) {
  await page.goto(`${appUrl}/medias/${meta.media_id_share_film}`, { waitUntil: "domcontentloaded", timeout: 60000 });
  await page.waitForResponse(
    (r) => r.url().includes(`/api/v1/hive/items/${meta.media_item_id_share_film}/sharing`) && r.ok(),
    { timeout: 20000 },
  );
  await page.locator(".film-file-panel").waitFor({ state: "visible", timeout: 15000 });
  const fileText = await page.locator(".film-file-panel").innerText();
  if (fileText.includes("Absent")) {
    throw new Error("ASSERT film share demo should show Présent (LibraryFile seed)");
  }
  await page.locator(".film-file-panel").getByRole("button", { name: /Plus d'actions/ }).click();
  await page.waitForTimeout(300);
  const fileMenu = page.getByRole("menu", { name: /Plus d'actions sur le fichier/i });
  await fileMenu.waitFor({ state: "visible", timeout: 10000 });
  try {
    await fileMenu.getByRole("menuitem", { name: /Choisir les ruches/i }).click({ timeout: 4000 });
  } catch {
    await page.evaluate(() => {
      const items = [...document.querySelectorAll('[role="menuitem"]')];
      const hit = items.find((el) => /Choisir les ruches/i.test(el.textContent || ""));
      hit?.dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true }));
    });
  }
  await page.locator("#hive-picker-title").waitFor({ state: "visible", timeout: 10000 });
}

async function applyFirstExclusion(page, itemId) {
  await page.evaluate(() => {
    const input = document.querySelector(".hive-picker input[type='checkbox']");
    if (input instanceof HTMLInputElement && input.checked) {
      input.click();
    }
    document.querySelector(".hive-picker-actions .btn.primary")?.dispatchEvent(
      new MouseEvent("click", { bubbles: true, cancelable: true }),
    );
  });
  await page.locator(".hive-picker-backdrop").waitFor({ state: "hidden", timeout: 10000 });
  const after = await page.evaluate(async (id) => {
    const r = await fetch(`/api/v1/hive/items/${id}/sharing`, { credentials: "include" });
    return r.json();
  }, itemId);
  console.log(
    `ASSERT_UI picker_persist excluded_ruche_ids_after_apply=${JSON.stringify(after.excluded_ruche_ids)}`,
  );
  if (!Array.isArray(after.excluded_ruche_ids) || after.excluded_ruche_ids.length < 1) {
    throw new Error("ASSERT picker persistence failed");
  }
}

await rm(demoDir, { recursive: true, force: true }).catch(() => {});
await runSeed(demoDir, { T8_UI_RUCHE_COUNT: "2" });

const meta = JSON.parse(await readFile(`${demoDir}/seed-meta.json`, "utf8"));
let uvicorn = startUvicorn();
const vite = spawn("npm", ["run", "dev", "--", "--host", "127.0.0.1", "--port", "5173", "--strictPort"], {
  cwd: path.join(root, "frontend"),
  stdio: "ignore",
  env: { ...process.env },
});
await new Promise((r) => setTimeout(r, 5000));
await mkdir(out, { recursive: true });
const browser = await chromium.launch();
try {
  const desk = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  await login(desk);
  await desk.goto(`${appUrl}/hive/catalogue`, { waitUntil: "networkidle" });
  await shot(desk, "t8-ui-catalogue-desktop.png");
  let body = await desk.innerText("body");
  if (!body.includes("Indisponible pour l")) throw new Error("ASSERT catalogue badge indisponible absent");

  await desk.goto(`${appUrl}/hive/catalogue/${meta.work_key_film}`, { waitUntil: "networkidle" });
  await shot(desk, "t8-ui-fiche-work-bandeau-desktop.png");
  body = await desk.innerText("body");
  if (!body.includes("Indisponible pour l")) throw new Error("ASSERT fiche oeuvre bandeau indisponible absent");

  await desk.goto(`${appUrl}/medias/${meta.media_id_film}`, { waitUntil: "domcontentloaded", timeout: 60000 });
  await desk.waitForTimeout(2000);
  await shot(desk, "t8-ui-fiche-media-bandeau-desktop.png");

  await desk.goto(`${appUrl}/hive/catalogue/${meta.work_key_series}`, { waitUntil: "networkidle" });
  await shot(desk, "t8-ui-menu-versions-desktop.png");

  await openPickerOnShareFilm(desk, meta);
  await assertPickerChrome(desk, { minRuches: 2, expectScroll: false });
  await shot(desk, "t8-ui-ruches-picker-dialog-desktop.png");
  await applyFirstExclusion(desk, meta.media_item_id_share_film);

  await desk.goto(`${appUrl}/reglages/hive`, { waitUntil: "networkidle" });
  await shot(desk, "t8-ui-reglages-ruches-desktop.png");

  const phone = await browser.newPage({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true });
  await login(phone);
  await phone.goto(`${appUrl}/hive/catalogue`, { waitUntil: "networkidle" });
  await shot(phone, "t8-ui-catalogue-phone.png");
  await openPickerOnShareFilm(phone, meta);
  await assertPickerChrome(phone, { minRuches: 2, expectScroll: false });
  await shot(phone, "t8-ui-ruches-picker-dialog-phone.png");
  await phone.keyboard.press("Escape");

  const scrollDir = `${demoDir}-scroll`;
  await rm(scrollDir, { recursive: true, force: true }).catch(() => {});
  await runSeed(scrollDir, { T8_UI_RUCHE_COUNT: "15" });
  const metaScroll = JSON.parse(await readFile(`${scrollDir}/seed-meta.json`, "utf8"));
  uvicorn.kill("SIGTERM");
  uvicorn = spawn(
    "python3",
    ["-m", "uvicorn", "mediatracker.api.app:app", "--host", "127.0.0.1", "--port", String(apiPort)],
    {
      cwd: root,
      env: {
        ...process.env,
        PYTHONPATH: `${root}/backend:${root}`,
        SECRET_KEY: "t8-demo-secret-key-012345678901",
        ADMIN_PASSWORD: "test-admin-password",
        DATABASE_URL: `sqlite:///${scrollDir}/app.db`,
        DATA_DIR: `${scrollDir}/data`,
        CONFIG_DIR: `${scrollDir}/config`,
        DISK_MIN_FREE_GB: "0",
      },
      stdio: "ignore",
    },
  );
  await new Promise((r) => setTimeout(r, 3000));
  const deskScroll = await browser.newPage({ viewport: { width: 1440, height: 640 } });
  await login(deskScroll);
  await openPickerOnShareFilm(deskScroll, metaScroll);
  await assertPickerChrome(deskScroll, { minRuches: 10, expectScroll: true });
  await shot(deskScroll, "t8-ui-ruches-picker-scroll-desktop.png");
} finally {
  await browser.close();
  uvicorn.kill("SIGTERM");
  vite.kill("SIGTERM");
}
console.log("t8 capture ok");
