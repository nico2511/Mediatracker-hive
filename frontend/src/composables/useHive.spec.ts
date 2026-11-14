import { beforeEach, describe, expect, it, vi } from "vitest";

import { hiveEnabledRef, refreshHiveState } from "./useHive";

const apiMock = vi.fn();
const fetchSettingsMock = vi.fn();

vi.mock("../api", () => ({
  api: (...args: unknown[]) => apiMock(...args),
}));

vi.mock("../settings", () => ({
  fetchSettings: () => fetchSettingsMock(),
}));

describe("refreshHiveState", () => {
  beforeEach(() => {
    apiMock.mockReset();
    fetchSettingsMock.mockReset();
  });

  it("reflete hive_enabled via overview", async () => {
    apiMock.mockResolvedValueOnce({ enabled: true });
    const hiveEnabled = hiveEnabledRef();
    await refreshHiveState();
    expect(hiveEnabled.value).toBe(true);
    apiMock.mockResolvedValueOnce({ enabled: false });
    await refreshHiveState();
    expect(hiveEnabled.value).toBe(false);
  });

  it("ne laisse pas un succès UI si overview indique désactivé après patch", async () => {
    apiMock.mockResolvedValueOnce({ enabled: false });
    const hiveEnabled = hiveEnabledRef();
    await refreshHiveState();
    expect(hiveEnabled.value).toBe(false);
  });
});
