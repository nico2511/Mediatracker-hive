import { chromium } from "playwright";
import { mkdir, readFile } from "node:fs/promises";
import { createHash } from "node:crypto";

const base = process.env.T7_APP_URL || "http://127.0.0.1:5173";
const out = "/opt/cursor/artifacts";

async function login(page) {
  await page.goto(`${base}/login`, { waitUntil: "networkidle" });
  await page.getByLabel(/Nom d.utilisateur/i).fill("admin");
  await page.getByLabel(/Mot de passe/i).fill("test-admin-password");
  await page.getByRole("button", { name: /Connexion|Se connecter/i }).click();
  await page.waitForTimeout(1500);
  if (page.url().includes("/configuration")) {
    throw new Error(`assistant installation encore actif: ${page.url()}`);
  }
}

async function assertHiveScreen(page, label) {
  if (page.url().includes("/configuration")) {
    throw new Error(`${label}: redirigé vers assistant (${page.url()})`);
  }
  const body = await page.innerText("body");
  if (body.includes("Première utilisation")) {
    throw new Error(`${label}: texte 'Première utilisation' détecté`);
  }
  if (!body.includes("Hive")) {
    throw new Error(`${label}: texte 'Hive' absent`);
  }
}

async function shot(page, path) {
  await page.screenshot({ path, fullPage: true });
  const buf = await readFile(path);
  const sha = createHash("sha256").update(buf).digest("hex").slice(0, 16);
  console.log(`saved ${path} sha256:${sha}`);
}

await mkdir(out, { recursive: true });
const browser = await chromium.launch();

const desktop = await browser.newPage({ viewport: { width: 1440, height: 900 } });
await login(desktop);
await desktop.goto(`${base}/reglages/hive`, { waitUntil: "networkidle" });
await assertHiveScreen(desktop, "desktop-reglages");
await shot(desktop, `${out}/t7-app-desktop-reglages.png`);
await desktop.goto(`${base}/hive/catalogue`, { waitUntil: "networkidle" });
await assertHiveScreen(desktop, "desktop-catalogue");
await shot(desktop, `${out}/t7-app-desktop-catalogue.png`);

const phone = await browser.newPage({
  viewport: { width: 390, height: 844 },
  isMobile: true,
  hasTouch: true,
});
await login(phone);
await phone.goto(`${base}/hive/catalogue`, { waitUntil: "networkidle" });
await assertHiveScreen(phone, "phone-catalogue");
await shot(phone, `${out}/t7-app-phone-catalogue.png`);
await phone.goto(`${base}/reglages/hive`, { waitUntil: "networkidle" });
await assertHiveScreen(phone, "phone-reglages");
await shot(phone, `${out}/t7-app-phone-reglages.png`);

await browser.close();
console.log("capture ok");
