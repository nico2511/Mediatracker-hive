import { describe, expect, it } from "vitest";

import { tmdbTrialRatingLabel } from "./tmdbTrialRating";

describe("tmdbTrialRatingLabel", () => {
  it("formate vote_average sur une décimale", () => {
    expect(tmdbTrialRatingLabel(7.256)).toBe("7,3");
    expect(tmdbTrialRatingLabel(null)).toBe("");
  });
});
