import { describe, expect, it } from "vitest";

import { tmdbBackdropSrc, tmdbPosterSrc } from "./tmdbImages";

const samplePath = "AbCdEf.jpg";

describe("tmdbBackdropSrc", () => {
  it("routes TMDB CDN URLs through the local backdrop cache", () => {
    const cdn = `https://image.tmdb.org/t/p/w780/${samplePath}`;
    expect(tmdbBackdropSrc(cdn)).toBe(`/api/v1/images/backdrop/w780/${encodeURIComponent(samplePath)}`);
  });

  it("defaults to w780 and allows another size", () => {
    const cdn = `https://image.tmdb.org/t/p/original/${samplePath}`;
    expect(tmdbBackdropSrc(cdn, "w342")).toBe(`/api/v1/images/backdrop/w342/${encodeURIComponent(samplePath)}`);
  });

  it("passes through non-TMDB URLs unchanged", () => {
    const local = "/api/v1/images/backdrop/w780/local.webp";
    expect(tmdbBackdropSrc(local)).toBe(local);
  });
});

describe("tmdbPosterSrc parity", () => {
  it("still routes poster URLs through the poster cache", () => {
    const cdn = `https://image.tmdb.org/t/p/w342/${samplePath}`;
    expect(tmdbPosterSrc(cdn)).toBe(`/api/v1/images/poster/w342/${encodeURIComponent(samplePath)}`);
  });
});