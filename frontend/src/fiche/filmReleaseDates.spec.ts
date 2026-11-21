import { describe, expect, it } from "vitest";

import {
  filmReleaseDisplayRegions,
  filmSearchFooter,
  filmSearchFooterParts,
  formatFilmReleaseShort,
  highlightRegionForSource,
} from "./filmReleaseDates";

describe("filmReleaseDates", () => {
  it("formate une date courte en français", () => {
    expect(formatFilmReleaseShort("2027-01-20")).toMatch(/20 janv\. 2027/);
  });

  it("affiche la région catalogue et les États-Unis", () => {
    expect(filmReleaseDisplayRegions("FR")).toEqual(["US", "FR"]);
    expect(filmReleaseDisplayRegions("DE")).toEqual(["US", "DE"]);
    expect(filmReleaseDisplayRegions("US")).toEqual(["US"]);
  });

  it("surligne le numérique US quand la source est digital_us", () => {
    expect(highlightRegionForSource("digital_us", "FR")).toEqual({ kind: "digital", region: "US" });
  });

  it("produit le pied de page recherche pour le numérique US", () => {
    const footer = filmSearchFooter("digital_us", "2027-01-20", "FR");
    expect(footer).toContain("Recherche des liens à partir du");
    expect(footer).toContain("États-Unis");
  });

  it("découpe le pied de page pour isoler la date", () => {
    const parts = filmSearchFooterParts("digital_us", "2027-01-20", "FR");
    expect(parts?.prefix).toContain("Recherche des liens à partir du ");
    expect(parts?.date).toMatch(/20 janv\. 2027/);
    expect(parts?.suffix).toContain("États-Unis");
  });

  it("formule digital_any sans pays catalogue", () => {
    const footer = filmSearchFooter("digital_any", "2027-02-01", "FR");
    expect(footer).toBe(
      "Recherche des liens à partir du 1 févr. 2027, première sortie numérique ou physique connue.",
    );
    expect(footer).not.toContain("France");
  });
});