import { flushPromises, mount } from "@vue/test-utils";
import { beforeEach, describe, expect, it, vi } from "vitest";

const apiMock = vi.hoisted(() => vi.fn());
const routerPush = vi.hoisted(() => vi.fn());

vi.mock("../api", async () => {
  const actual = await vi.importActual<typeof import("../api")>("../api");
  return { ...actual, api: apiMock };
});
vi.mock("../settings", () => ({ fetchSettings: vi.fn(), patchSettings: vi.fn() }));
vi.mock("../session", () => ({
  clearSession: vi.fn(),
  noteSetupCompleted: vi.fn(),
}));
vi.mock("vue-router", () => ({
  useRouter: () => ({ push: routerPush }),
}));

import SetupView from "./SetupView.vue";

const LOG_PLACEHOLDER =
  "Aucun compte administrateur : pour créer le premier compte via l'assistant, " +
  "saisissez le jeton d'installation suivant (ou définissez SETUP_TOKEN pour le fixer) : xxxx-xxxx";

async function mountSetupStep0() {
  apiMock.mockReset();
  apiMock.mockResolvedValue({ needs_password: true, completed: false });
  const w = mount(SetupView, { attachTo: document.body });
  await flushPromises();
  return w;
}

beforeEach(() => {
  routerPush.mockClear();
});

describe("SetupView — aide jeton d'installation (étape mot de passe)", () => {
  it("affiche le texte obligatoire sous le champ", async () => {
    const w = await mountSetupStep0();
    const help = w.get("[data-testid='setup-token-help']");
    expect(help.text()).toBe("Obligatoire. Ce jeton autorise la création du premier compte administrateur.");
    w.unmount();
  });

  it("encadré replié par défaut puis dépliable", async () => {
    const w = await mountSetupStep0();
    const details = w.get("[data-testid='setup-token-where']").element as HTMLDetailsElement;
    expect(details.open).toBe(false);

    await w.find("[data-testid='setup-token-where'] summary").trigger("click");
    expect(details.open).toBe(true);

    const panel = w.get("[data-testid='setup-token-where']");
    expect(panel.text()).toContain("Dockge");
    expect(panel.text()).toContain(LOG_PLACEHOLDER);
    expect(panel.text()).toContain("SETUP_TOKEN");
    expect(panel.text()).not.toMatch(/[A-Za-z0-9_-]{24,}/);

    w.unmount();
  });
});
