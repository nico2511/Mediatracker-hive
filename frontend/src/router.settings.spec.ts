import { describe, expect, it } from "vitest";

import router from "./router";

describe("router — réglages", () => {
  it("redirige nettoyage vers corbeille", () => {
    const reglages = router.options.routes.find((row) => row.path === "/")?.children?.find((c) => c.path === "reglages");
    expect(reglages).toBeDefined();
    const children = reglages?.children ?? [];
    const redirect = children.find((row) => row.path === "nettoyage");
    expect(redirect?.redirect).toBe("/reglages/corbeille");
  });
});