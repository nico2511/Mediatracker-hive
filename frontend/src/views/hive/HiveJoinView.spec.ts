import { flushPromises, mount } from "@vue/test-utils";
import { beforeEach, describe, expect, it, vi } from "vitest";

const apiMock = vi.hoisted(() => vi.fn());
const routerPush = vi.hoisted(() => vi.fn());

vi.mock("../../api", () => ({
  api: (...args: unknown[]) => apiMock(...args),
  userFacingApiError: (err: unknown, fallback: string) =>
    err instanceof Error ? err.message : fallback,
}));

vi.mock("vue-router", () => ({
  useRouter: () => ({ push: routerPush }),
}));

import HiveJoinView from "./HiveJoinView.vue";
import { clearHiveJoinWait, writeHiveJoinWait } from "../../hive/hiveJoinSession";

const SAMPLE_LINK =
  "mtrhive:1:eyJ2IjoxLCJydWNoZV9pZCI6ImFiY2RlZjEyMzQ1Njc4OWFiY2RlZjEyMzQ1Njc4OWFiY2RlZjEyMzQ1IiwiY3JlYXRvcl9pZCI6Imluc3QtZGVtby1jciIsImNyZWF0b3JfYWRkcmVzcyI6Imh0dHBzOi8vbXRyLmV4ZW1wbGUuZnIiLCJ0b2tlbl9iNjQiOiJaR1Z0YnkxMGIydGxiZz09IiwiZXhwaXJlcyI6IjIwMjYtMTItMDFUMDA6MDA6MDBaIiwic2lnbmF0dXJlX2I2NCI6IlpHVnRieTF6YVdjPSJ9";

beforeEach(() => {
  clearHiveJoinWait();
  apiMock.mockReset();
  routerPush.mockReset();
});

describe("HiveJoinView", () => {
  it("affiche l’attente et le code après begin sans quitter l’écran", async () => {
    apiMock.mockImplementation(async (url: string) => {
      if (url === "/api/v1/hive/join/preview") {
        return { ruche_id_hex: "ab".repeat(32), member_count: 2 };
      }
      if (url === "/api/v1/hive/identity") {
        return { name: "Invitee", public_address: "" };
      }
      if (url === "/api/v1/hive/join/begin") {
        return {
          control_code: "AB12CD34",
          pending_id: 7,
          invitee_instance_id: "inv-1",
          creator_base_url: "https://mtr.exemple.fr",
          ruche_id_hex: "ab".repeat(32),
        };
      }
      throw new Error(`unexpected ${url}`);
    });

    const w = mount(HiveJoinView);
    await w.get("[data-testid=hive-invite-link]").setValue(SAMPLE_LINK);
    await w.get("[data-testid=hive-preview]").trigger("click");
    await flushPromises();
    await w.get("[data-testid=hive-begin]").trigger("click");
    await flushPromises();

    expect(w.get("[data-testid=hive-control-code]").text()).toBe("AB12CD34");
    expect(routerPush).not.toHaveBeenCalled();
    expect(w.find("[data-testid=hive-confirm-join]").exists()).toBe(true);
    const stored = localStorage.getItem("mediatracker:hive-join-wait") || "";
    expect(stored).not.toContain("mtrhive");
    expect(stored).not.toContain('"link"');
  });

  it("restaure l’attente depuis le stockage local", async () => {
    writeHiveJoinWait({
      ruche_id_hex: "ab".repeat(32),
      creator_id: "inst-demo-cr",
      creator_base_url: "https://mtr.exemple.fr",
      control_code: "ZZ99YY88",
    });
    const w = mount(HiveJoinView);
    await flushPromises();
    expect(w.get("[data-testid=hive-control-code]").text()).toBe("ZZ99YY88");
  });
});
