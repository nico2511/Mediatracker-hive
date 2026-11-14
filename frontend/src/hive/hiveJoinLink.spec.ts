import { describe, expect, it } from "vitest";

import { parseInvitationLink } from "./hiveJoinLink";

const SAMPLE =
  "mtrhive:1:eyJ2IjoxLCJydWNoZV9pZCI6ImFiY2RlZjEyMzQ1Njc4OWFiY2RlZjEyMzQ1Njc4OWFiY2RlZjEyMzQ1IiwiY3JlYXRvcl9pZCI6Imluc3QtZGVtby1jciIsImNyZWF0b3JfYWRkcmVzcyI6Imh0dHBzOi8vbXRyLmV4ZW1wbGUuZnIiLCJ0b2tlbl9iNjQiOiJaR1Z0YnkxMGIydGxiZz09IiwiZXhwaXJlcyI6IjIwMjYtMTItMDFUMDA6MDA6MDBaIiwic2lnbmF0dXJlX2I2NCI6IlpHVnRieTF6YVdjPSJ9";

describe("parseInvitationLink", () => {
  it("extrait ruche et créateur", () => {
    const fields = parseInvitationLink(SAMPLE);
    expect(fields?.ruche_id_hex).toBe("abcdef123456789abcdef123456789abcdef12345");
    expect(fields?.creator_id).toBe("inst-demo-cr");
    expect(fields?.creator_address).toBe("https://mtr.exemple.fr");
  });

  it("refuse un préfixe incorrect", () => {
    expect(parseInvitationLink("http://bad")).toBeNull();
  });
});
