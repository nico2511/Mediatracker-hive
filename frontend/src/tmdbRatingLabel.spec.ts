import { describe, expect, it } from "vitest";

import {
  tmdbRatingCardAriaLabel,
  tmdbRatingLabel,
  tmdbVoteCountLabel,
  tmdbVoteCountPhrase,
} from "./tmdbRatingLabel";

describe("tmdbRatingLabel", () => {
  it("formate vote_average sur une décimale", () => {
    expect(tmdbRatingLabel(7.256)).toBe("7,3");
    expect(tmdbRatingLabel(null)).toBe("");
  });
});

describe("tmdbRatingCardAriaLabel", () => {
  it("libellé accessible pour carte", () => {
    expect(tmdbRatingCardAriaLabel(7.4)).toBe("Note TMDB 7,4 sur 10");
    expect(tmdbRatingCardAriaLabel(undefined)).toBe("");
  });
});

describe("tmdbVoteCountLabel", () => {
  it("formate le nombre de votes", () => {
    expect(tmdbVoteCountLabel(1240)).toMatch(/^1.240$/);
    expect(tmdbVoteCountLabel(null)).toBe("");
  });
});

describe("tmdbVoteCountPhrase", () => {
  it("utilise le singulier pour un vote", () => {
    expect(tmdbVoteCountPhrase(1)).toBe("1 vote");
    expect(tmdbVoteCountPhrase(1240)).toMatch(/^1.240 votes$/);
  });
});