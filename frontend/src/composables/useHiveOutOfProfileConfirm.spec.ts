import { describe, expect, it, vi } from "vitest";

import { useHiveOutOfProfileConfirm } from "./useHiveOutOfProfileConfirm";

describe("useHiveOutOfProfileConfirm", () => {
  it("demande deux pas avant un choix hors profil", () => {
    const { beginOrConfirm, needsConfirm } = useHiveOutOfProfileConfirm();
    const run = vi.fn();
    const ver = { profile_compatible: false };

    beginOrConfirm("ep-1", ver, run);
    expect(run).not.toHaveBeenCalled();
    expect(needsConfirm("ep-1", ver)).toBe(true);

    beginOrConfirm("ep-1", ver, run);
    expect(run).toHaveBeenCalledOnce();
  });
});