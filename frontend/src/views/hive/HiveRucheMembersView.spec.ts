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
        members: [
          { instance_id: "cr-1", display_name: "Creator", pull_only: false, is_self: true },
          { instance_id: "inv-42", display_name: "Invitee", pull_only: false, is_self: false },
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
    return {};
  });
});

import HiveRucheMembersView from "./HiveRucheMembersView.vue";

async function mountMembers() {
  const w = mount(HiveRucheMembersView);
  await flushPromises();
  return w;
}

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
