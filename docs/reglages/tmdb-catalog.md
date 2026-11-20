# Catalogue TMDB

URL : `/reglages/tmdb-catalog`

Cette page règle la **langue d’affichage**, la **région** et un filtre optionnel sur les **langues originales** pour les écrans alimentés par l’API TMDB (Accueil, Découvrir, plateformes, genres).

## Réglages

| Clé | Rôle | Défaut |
|-----|------|--------|
| `tmdb_display_language` | Libellés TMDB (métadonnées, recherche, discover) au format `xx-YY` | `fr-FR` |
| `tmdb_region` | Code pays ISO 3166-1 alpha-2 (dates de sortie, `watch_region`, fournisseurs) | `FR` |
| `tmdb_original_languages` | Liste ISO 639-1 séparée par des virgules ; vide = toutes les langues | *(vide)* |

## Effet dans l’application

- **Accueil** et **Découvrir** : discover TMDB avec la région et, si des langues sont cochées, le paramètre `with_original_language` ou un filtre côté serveur sur les résultats de recherche catalogue.
- **Identification** (bibliothèque, recherche `/metadata/tmdb`, rattachement de fiche) : **non filtrée** par les langues originales ; seule la langue d’affichage s’applique.
- **Jeton TMDB** : toujours sur la page [API et jetons](jetons.md).

## Distinction

Le réglage `catalog_language` (page Sources) concerne les **versions de release** des sources (VFF, VOSTFR, etc.), pas l’affichage TMDB.
