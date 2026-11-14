import { describe, expect, it } from "vitest";

import router from "./router";
import TmdbHomeView from "./views/TmdbHomeView.vue";
import TmdbCatalogView from "./views/TmdbCatalogView.vue";

describe("router TMDB promotion", () => {
  it("redirige les URL d'essai TMDB vers les routes définitives", () => {
    const routes = router.getRoutes();
    expect(routes.find((row) => row.path === "/tmdb")?.redirect).toBe("/");
    expect(routes.find((row) => row.path === "/tmdb/films")?.redirect).toBe("/nouveautes/films");
    expect(routes.find((row) => row.path === "/tmdb/series")?.redirect).toBe("/nouveautes/series");
  });

  it("sert l'accueil et les catalogues TMDB sur les routes définitives", () => {
    const home = router.resolve("/");
    expect(home.matched.at(-1)?.components?.default).toBe(TmdbHomeView);

    const films = router.resolve("/nouveautes/films");
    expect(films.matched.at(-1)?.components?.default).toBe(TmdbCatalogView);

    const series = router.resolve("/nouveautes/series");
    expect(series.matched.at(-1)?.components?.default).toBe(TmdbCatalogView);
  });
});
