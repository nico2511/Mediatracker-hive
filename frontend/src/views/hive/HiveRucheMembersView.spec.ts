import { flushPromises, mount } from "@vue/test-utils";
import { beforeEach, describe, expect, it, vi } from "vitest";

const apiMock = vi.hoisted(() => vi.fn());

vi.mock("../../api", () => ({
  api: (...args: unknown[]) => apiMock(...args),
  userFacingApiError: (_err: unknown, fallback: string) => fallback,
}));

const RUCHE = "cd".repeat(32);

vi.mock("vue-router", () => ({
  useRoute: () => ({ params: { rucheIdHex: "cd".repeat(32) } }),
  RouterLink: { template: "<a><slot /></a>" },
}));
const PENDING_ID = 3;

beforeEach(() => {
  apiMock.mockReset();
  apiMock.mockImplementation(async (url: string, opts?: { method?: string }) => {
    if (url === `/api/v1/hive/ruches/${RUCHE}/detail`) {
      return {
        ruche_id_hex: RUCHE,
        is_creator: true,
        weekly_quota_bytes: 100 * 1024 ** 3,
        catalog_epoch: 0,
        catalog_reset_at: null,
        members: [
          {
            instance_id: "cr-1",
            display_name: "Creator",
            pull_only: false,
            is_self: true,
            app_version: "3.0.0@abc12345",
            hive_protocol: 1,
            capabilities: [],
          },
          {
            instance_id: "inv-42",
            display_name: "Invitee",
            pull_only: false,
            is_self: false,
            app_version: "2.5.0@deadbeef",
            hive_protocol: 1,
            capabilities: [],
          },
          {
            instance_id: "inv-old",
            display_name: "Legacy",
            pull_only: false,
            is_self: false,
            app_version: "2.0.0",
            hive_protocol: 0,
            capabilities: [],
          },
          {
            instance_id: "inv-unk",
            display_name: "Unknown",
            pull_only: false,
            is_self: false,
            app_version: null,
            hive_protocol: 0,
            capabilities: [],
          },
        ],
      };
    }
    if (url === `/api/v1/hive/join/pending/${RUCHE}`) {
      return [
        {
          pending_id: PENDING_ID,
          invitee_instance_id: "inv-42",
          invitee_name: "Invitee",
          control_code: "QW12ER34",
        },
      ];
    }
    if (url === "/api/v1/hive/join/accept" && opts?.method === "POST") {
      return undefined;
    }
    if (url === "/api/v1/hive/join/reject" && opts?.method === "POST") {
      return undefined;
    }
    if (url === `/api/v1/hive/ruches/${RUCHE}/catalog-reset` && opts?.method === "POST") {
      return undefined;
    }
  return {};
  });
});

import { hiveRucheConfirmName } from "../../hive/hiveRucheLabel";

import HiveRucheMembersView from "./HiveRucheMembersView.vue";

async function mountMembers() {
  const w = mount(HiveRucheMembersView);
  await flushPromises();
  return w;
}

describe("HiveRucheMembersView — versions", () => {
  it("affiche l’annonce locale, à jour, ancienne et inconnue", async () => {
    const w = await mountMembers();
    expect(w.get("[data-testid=hive-self-version-announce]").text()).toContain("v3.0.0");
    const pills = w.findAll("[data-testid=hive-member-version-pill]");
    const texts = pills.map((p) => p.text());
    expect(texts.some((t) => t.includes("v3.0.0") && t.includes("à jour"))).toBe(true);
    expect(texts.some((t) => t.includes("v2.5.0") && t.includes("à jour"))).toBe(true);
    expect(texts.some((t) => t.includes("v2.0.0") && t.includes("ancienne version"))).toBe(true);
    expect(texts.some((t) => t.includes("version inconnue"))).toBe(true);
    const upToDate = pills.find((p) => p.text().includes("v2.5.0"));
    expect(upToDate?.attributes("title")).toContain("@deadbeef");
  });
});

describe("HiveRucheMembersView — reset catalogue", () => {
  it("affiche la boîte et n’envoie qu’après saisie du nom exact", async () => {
    const w = await mountMembers();
    expect(w.find("[data-testid=hive-catalog-reset-zone]").exists()).toBe(true);
    await w.get("[data-testid=hive-catalog-reset-open]").trigger("click");
    await flushPromises();
    const dialog = document.body.querySelector("[data-testid=hive-catalog-reset-dialog]");
    expect(dialog?.textContent).toContain("Effacé");
    expect(dialog?.textContent).toContain("Conservé");
    const submit = document.body.querySelector(
      "[data-testid=hive-catalog-reset-submit]",
    ) as HTMLButtonElement | null;
    expect(submit?.disabled).toBe(true);
    const label = hiveRucheConfirmName(RUCHE);
    const input = document.body.querySelector("[data-testid=hive-catalog-reset-input]") as HTMLInputElement;
    input.value = label;
    input.dispatchEvent(new Event("input"));
    await flushPromises();
    expect((document.body.querySelector("[data-testid=hive-catalog-reset-submit]") as HTMLButtonElement).disabled).toBe(
      false,
    );
    submit?.click();
    await flushPromises();
    expect(apiMock).toHaveBeenCalledWith(
      `/api/v1/hive/ruches/${RUCHE}/catalog-reset`,
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ confirm_name: label }),
      }),
    );
  });

  it("affiche l’avis discret pour un membre non créateur", async () => {
    apiMock.mockImplementation(async (url: string) => {
      if (url === `/api/v1/hive/ruches/${RUCHE}/detail`) {
        return {
          ruche_id_hex: RUCHE,
          is_creator: false,
          weekly_quota_bytes: 100 * 1024 ** 3,
          catalog_epoch: 2,
          catalog_reset_at: "2026-03-15T12:00:00+00:00",
          members: [],
        };
      }
      return {};
    });
    const w = mount(HiveRucheMembersView);
    await flushPromises();
    const notice = w.get("[data-testid=hive-catalog-reset-notice]");
    expect(notice.text()).toContain("15/03");
    expect(w.find("[data-testid=hive-catalog-reset-zone]").exists()).toBe(false);
  });
});

describe("HiveRucheMembersView — adhésion", () => {
  it("n’affiche pas le code attendu et garde Accepter inactif sans confirmation", async () => {
    const w = await mountMembers();
    const list = w.get("[data-testid=hive-pending-list]");
    expect(list.text()).toContain("Invitee");
    expect(list.text()).not.toContain("QW12ER34");
    expect(w.get("[data-testid=hive-accept-pending]").attributes("disabled")).toBeDefined();
  });

  it("accepte seulement après code valide et case cochée", async () => {
    const w = await mountMembers();
    await w.get(`[data-testid=hive-pending-code-input-${PENDING_ID}]`).setValue("QW12ER34");
    await w.get(`[data-testid=hive-pending-ack-${PENDING_ID}]`).setValue(true);
    await flushPromises();
    expect(w.get("[data-testid=hive-accept-pending]").attributes("disabled")).toBeUndefined();
    await w.get("[data-testid=hive-accept-pending]").trigger("click");
    await flushPromises();
    expect(apiMock).toHaveBeenCalledWith(
      "/api/v1/hive/join/accept",
      expect.objectContaining({ method: "POST" }),
    );
  });

  it("refuse une demande via l’endpoint dédié", async () => {
    const w = await mountMembers();
    await w.get("[data-testid=hive-reject-pending]").trigger("click");
    await flushPromises();
    expect(apiMock).toHaveBeenCalledWith(
      "/api/v1/hive/join/reject",
      expect.objectContaining({ method: "POST", body: JSON.stringify({ pending_id: PENDING_ID }) }),
    );
  });
});
