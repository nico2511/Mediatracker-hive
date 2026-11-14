/**
 * Vérification run5 — démo locale, sans rechargement forcé entre cas.
 */
import { chromium } from "playwright";
import { mkdirSync, writeFileSync } from "node:fs";

const base = process.env.T7_APP_URL || "http://127.0.0.1:5173";
const outDir = "/opt/cursor/artifacts";
const results = [];

function record(id, status, detail) {
  results.push({ id, status, ...detail });
}

async function login(page) {
  await page.goto(`${base}/login`, { waitUntil: "networkidle" });
  await page.getByLabel(/Nom d'utilisateur/i).fill("admin");
  await page.getByLabel(/Mot de passe/i).fill("test-admin-password");
  await page.getByRole("button", { name: /Se connecter/i }).click();
  await page.waitForTimeout(1200);
}

async function sidebarHiveCount(page) {
  return page.locator("nav .sidebar-link").filter({ hasText: "Hive" }).count();
}

/** Bascule le toggle ; complète la modale « Première activation » si l’UI l’ouvre. */
async function setHiveToggle(page, wantOn) {
  try {
    const t = page.getByTestId("hive-master-toggle");
    await t.waitFor({ state: "visible", timeout: 10000 });
    await t.scrollIntoViewIfNeeded();
    const is = await t.isChecked();
    if (is === wantOn) {
      return { skipped: true, status: null, path: "noop" };
    }
    const patchWait = page
      .waitForResponse(
        (r) => r.url().includes("/api/v1/settings") && r.request().method() === "PATCH",
        { timeout: 20000 },
      )
      .catch(() => null);
    await t.click();
    let path = "toggle_patch";
    let resp = await patchWait;
    if (!resp) {
      const modal = page.locator("section.hive-modal").filter({ hasText: "Première activation" });
      if (await modal.isVisible().catch(() => false)) {
        path = "activation_modal_after_toggle";
        await modal.locator("input.input").first().fill("Instance fictive");
        try {
          const modalPatch = page
            .waitForResponse(
              (r) => r.url().includes("/api/v1/settings") && r.request().method() === "PATCH",
              { timeout: 20000 },
            )
            .catch(() => null);
          await modal.getByRole("button", { name: "Activer Hive", exact: true }).click();
          resp = await modalPatch;
          if (!resp) {
            throw new Error("activation_modal_no_patch");
          }
        } catch (err) {
          return {
            skipped: false,
            status: 0,
            path: "activation_modal_timeout",
            error: String(err?.message || err),
          };
        }
      } else {
        return { skipped: false, status: 0, path: "no_patch_no_modal", error: "no_patch_response" };
      }
    }
    return { skipped: false, status: resp?.status?.() ?? 0, resp, path };
  } catch (err) {
    return { skipped: false, status: 0, path: "toggle_error", error: String(err?.message || err) };
  }
}

async function apiOverview(page) {
  return page.evaluate(async () => {
    const r = await fetch("/api/v1/hive/overview", { credentials: "include" });
    return { status: r.status, body: await r.json() };
  });
}

async function ensureHiveOn(page) {
  await page.goto(`${base}/reglages/hive`, { waitUntil: "networkidle" });
  let ov = await apiOverview(page);
  if (ov.body?.enabled) {
    return ov;
  }
  const open = page.getByRole("button", { name: "Activer Hive…" });
  if (await open.count()) {
    await open.click();
    await page.locator(".hive-modal input.input").first().fill("Instance fictive");
    const respP = page.waitForResponse(
      (r) => r.url().includes("/api/v1/settings") && r.request().method() === "PATCH",
      { timeout: 20000 },
    );
    await page.getByRole("button", { name: "Activer Hive", exact: true }).click();
    const p = await respP;
    await page.waitForTimeout(600);
    ov = await apiOverview(page);
    return { ...ov, bootstrapPatch: p.status() };
  }
  await page.getByTestId("hive-master-toggle").check();
  await page.waitForTimeout(800);
  return apiOverview(page);
}

const browser = await chromium.launch();
mkdirSync(outDir, { recursive: true });

// --- Desktop ---
const desk = await browser.newPage({ viewport: { width: 1400, height: 900 } });
await login(desk);
const toggle = () => desk.getByTestId("hive-master-toggle");

await ensureHiveOn(desk);
const identityNameAtStart = await desk.evaluate(async () => {
  const r = await fetch("/api/v1/hive/identity", { credentials: "include" });
  if (!r.ok) return null;
  const b = await r.json();
  return b.name ?? null;
});

async function runCase(id, fn) {
  try {
    await fn();
  } catch (err) {
    record(id, "FAIL", { error: String(err?.message || err) });
  }
}

// C. Sauvegarde complète multi-champs (saveCore) — avant désactivation
await runCase("C_full_save_multi_field", async () => {
  await desk.goto(`${base}/reglages/hive`, { waitUntil: "networkidle" });
  const btn = desk.getByRole("button", { name: /Enregistrer les réglages Hive/i });
  const visible = await btn.count();
  if (visible === 0) {
    record("C_full_save_multi_field", "NON_TESTE", { reason: "bouton absent (Hive off ou section masquée)" });
  } else {
    const respP = desk.waitForResponse(
      (r) => r.url().includes("/api/v1/settings") && r.request().method() === "PATCH",
      { timeout: 20000 },
    );
    await btn.click();
    const resp = await respP;
    await desk.waitForTimeout(500);
    const ov = await apiOverview(desk);
    const note = await desk.locator(".ok").first().textContent().catch(() => "");
    const err = await desk.locator(".settings-hive .error[role='alert']").first().textContent().catch(() => "");
    const pass = resp.status() === 200 && ov.body?.enabled === true && /enregistr/i.test(note || "") && !err;
    await desk.screenshot({ path: `${outDir}/t7-run5-full-save.png`, fullPage: true });
    let patchBody = null;
    try {
      patchBody = JSON.parse(resp.request().postData() || "{}");
    } catch {
      patchBody = null;
    }
    record("C_full_save_multi_field", pass ? "PASS" : "FAIL", {
      patchStatus: resp.status(),
      patchKeys: patchBody?.values ? Object.keys(patchBody.values) : null,
      overview: ov,
      note: (note || "").trim(),
      error: (err || "").trim(),
    });
  }
});

// D. Échec sauvegarde : pas de faux succès + resync UI
await runCase("D_save_failure_no_false_success", async () => {
  await desk.goto(`${base}/reglages/hive`, { waitUntil: "networkidle" });
  const before = await apiOverview(desk);
  await desk.route("**/api/v1/settings", async (route) => {
    if (route.request().method() === "PATCH") {
      await route.fulfill({ status: 500, contentType: "application/json", body: '{"detail":"échec simulé"}' });
      return;
    }
    await route.continue();
  });
  const btn = desk.getByRole("button", { name: /Enregistrer les réglages Hive/i });
  if ((await btn.count()) === 0) {
    record("D_save_failure_no_false_success", "NON_TESTE", { reason: "bouton absent" });
  } else {
    await btn.click();
    await desk.waitForTimeout(600);
    const note = await desk.locator(".ok").first().textContent().catch(() => "");
    const err = await desk.locator(".settings-hive .error[role='alert']").first().textContent().catch(() => "");
    const after = await apiOverview(desk);
    const checked = await toggle().isChecked();
    const pass =
      !/enregistr/i.test(note || "") &&
      Boolean((err || "").trim()) &&
      after.body?.enabled === before.body?.enabled &&
      checked === (before.body?.enabled === true);
    await desk.screenshot({ path: `${outDir}/t7-run5-save-fail.png`, fullPage: true });
    record("D_save_failure_no_false_success", pass ? "PASS" : "FAIL", {
      note: (note || "").trim(),
      error: (err || "").trim(),
      overviewBefore: before,
      overviewAfter: after,
      toggleChecked: checked,
    });
  }
  await desk.unroute("**/api/v1/settings");
});

// A. Désactivation sans reload
await runCase("A_desktop_disable_no_reload", async () => {
  await desk.goto(`${base}/reglages/hive`, { waitUntil: "networkidle" });
  const flip = await setHiveToggle(desk, false);
  const respStatus = flip.status ?? 0;
  await desk.waitForTimeout(400);
  const nav = await sidebarHiveCount(desk);
  const ov = await apiOverview(desk);
  await desk.goto(`${base}/hive/catalogue`, { waitUntil: "networkidle" });
  await desk.waitForTimeout(500);
  const url = desk.url();
  const pass = respStatus === 200 && ov.body?.enabled === false && nav === 0 && url.includes("/reglages/hive");
  await desk.screenshot({ path: `${outDir}/t7-run5-desktop-off.png`, fullPage: true });
  record("A_desktop_disable_no_reload", pass ? "PASS" : "FAIL", {
    patchStatus: respStatus,
    toggleSkipped: flip.skipped,
    enablePath: flip.path,
    navHiveLinks: nav,
    overview: ov,
    urlAfterHiveNav: url,
  });
});

// B. Réactivation sans reload
await runCase("B_desktop_enable_no_reload", async () => {
  await desk.goto(`${base}/reglages/hive`, { waitUntil: "networkidle" });
  const flip = await setHiveToggle(desk, true);
  const respStatus = flip.status ?? 0;
  await desk.waitForTimeout(400);
  const nav = await sidebarHiveCount(desk);
  const ov = await apiOverview(desk);
  await desk.goto(`${base}/hive/catalogue`, { waitUntil: "networkidle" });
  await desk.waitForTimeout(500);
  const onHive = desk.url().includes("/hive/");
  const pass = respStatus === 200 && ov.body?.enabled === true && nav >= 1 && onHive;
  await desk.screenshot({ path: `${outDir}/t7-run5-desktop-on.png`, fullPage: true });
  const toggleOnly = flip.path === "toggle_patch" && respStatus === 200;
  const identityAfter = await desk.evaluate(async () => {
    const r = await fetch("/api/v1/hive/identity", { credentials: "include" });
    if (!r.ok) return null;
    return (await r.json()).name ?? null;
  });
  const passWithName =
    pass && identityAfter === identityNameAtStart && identityNameAtStart != null;
  record("B_desktop_enable_no_reload", passWithName ? "PASS" : "FAIL", {
    patchStatus: respStatus,
    toggleSkipped: flip.skipped,
    enablePath: flip.path,
    toggleOnlyReenable: toggleOnly,
    navHiveLinks: nav,
    overview: ov,
    urlAfterHiveNav: desk.url(),
    error: flip.error,
    identityNameAtStart,
    identityNameAfter: identityAfter,
  });
});

// E. Mobile : menu sidebar via hamburger
await runCase("E_mobile_menu_toggle", async () => {
  const phone = await browser.newPage({
    viewport: { width: 390, height: 844 },
    isMobile: true,
    hasTouch: true,
  });
  await login(phone);
  await ensureHiveOn(phone);
  await phone.goto(`${base}/reglages/hive`, { waitUntil: "networkidle" });
  const mobToggle = phone.getByTestId("hive-master-toggle");
  const menuOpen = async () =>
    phone.locator("aside.sidebar").evaluate((el) => el.classList.contains("menu-open"));
  const openMenu = async () => {
    if (!(await menuOpen())) {
      await phone.locator("button.menu-toggle").click();
      await phone.waitForTimeout(400);
    }
  };
  const closeMenu = async () => {
    if (await menuOpen()) {
      await phone.locator("button.menu-toggle").click();
      await phone.waitForTimeout(400);
    }
  };
  await openMenu();
  let navOn = await phone.locator("nav .sidebar-link").filter({ hasText: "Hive" }).count();
  await closeMenu();
  const offFlip = await setHiveToggle(phone, false);
  await phone.waitForTimeout(500);
  const overviewAfterOff = await apiOverview(phone);
  await openMenu();
  let navOff = await phone.locator("nav .sidebar-link").filter({ hasText: "Hive" }).count();
  await closeMenu();
  const onFlip = await setHiveToggle(phone, true);
  await phone.waitForTimeout(800);
  await openMenu();
  let navOn2 = await phone.locator("nav .sidebar-link").filter({ hasText: "Hive" }).count();
  const pass = navOn >= 1 && navOff === 0 && navOn2 >= 1;
  await phone.screenshot({ path: `${outDir}/t7-run5-phone-nav.png`, fullPage: true });
  record("E_mobile_menu_toggle", pass ? "PASS" : "FAIL", {
    navOn,
    navOff,
    navOn2,
    offPath: offFlip.path,
    offError: offFlip.error,
    offPatchStatus: offFlip.status,
    onPath: onFlip.path,
    onPatchStatus: onFlip.status,
    overviewAfterOff,
  });
  await phone.close();
});

await browser.close();

  const report = {
  sha: process.env.T7_EXPECT_SHA || "unknown",
  base,
  at: new Date().toISOString(),
  results,
  summary: {
    pass: results.filter((r) => r.status === "PASS").length,
    fail: results.filter((r) => r.status === "FAIL").length,
    non_teste: results.filter((r) => r.status === "NON_TESTE").length,
  },
};
writeFileSync(`${outDir}/t7-run5-verify.json`, JSON.stringify(report, null, 2));
console.log(JSON.stringify(report, null, 2));
