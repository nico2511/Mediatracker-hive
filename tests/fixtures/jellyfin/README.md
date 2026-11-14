# Fixtures Jellyfin 10.11 anonymisées (MediaTracker — import d'historique)

Toutes les données sont **fictives** (utilisateur `utilisateur-test`, titres « Exemple », GUID générés en uuid5,
IP de documentation 192.0.2.x, ids Tmdb/Imdb/Tvdb en 99xxxx). Seule la **structure** est copiée d'une vraie
instance Jellyfin 10.11 (DDL exact, ordre des clés JSON, formats). Voir `STRUCTURE.md` pour la description complète.

Régénération : `python3 ../generer_fixtures.py` (le script lit le DDL et la séquence des clés JSON dans la vraie copie,
mais n'écrit que des valeurs fictives ; il n'est pas dans `fixtures/`).

## Fichiers

| Fichier | Contenu |
|---|---|
| `jellyfin-10.11-anonyme.db` | SQLite avec le DDL réel (+ index) de `Users`, `BaseItems`, `BaseItemProviders`, `UserData`, `ActivityLogs` (+ `sqlite_sequence`). GUID en MAJUSCULES, dates `YYYY-MM-DD HH:MM:SS.fffffff`. |
| `jellyfin-backup-anonyme.zip` | Même jeu de données au format backup 10.11 : `Database/{ActivityLogs,Users,UserData,BaseItems,BaseItemProviders}.json` + `manifest.json` (dernière entrée). JSON compact sur une ligne, échappement `\uXXXX`, GUID minuscules, dates ISO `Z`, navigations `null`. Les autres fichiers du vrai backup (Config/, Root/, ScheduledTasks, autres tables) sont omis. |
| `STRUCTURE.md` | Structure réelle relevée (DDL, formats, gabarits de journal, stats, arborescence et forme JSON du zip). |

Les deux fixtures contiennent **exactement les mêmes lignes**.

## Jeu de données

Utilisateur : `utilisateur-test`, Id `C881E852-9C27-5FD8-97A2-AD79EE8FC142`.

Bibliothèque (encore présente) :

| Code | Type | Nom | Ids | État UserData |
|---|---|---|---|---|
| M1 | Film | Film Exemple 1 | tmdb 900001, tt9900001 | vu |
| M2 | Film | Film Exemple 2 - Le Retour | tmdb 900002, tt9900002 | vu, PlayCount 2 |
| M3 | Film | Film Exemple 3 de la Nuit | tmdb 900003, tt9900003 | en cours (position, non vu) |
| M4 | Film | Film Exemple 4 | tmdb 900004, tt9900004 | aucune donnée |
| M5 | Film | L'Exemple & Cie | tmdb 900005, tt9900005 | vu |
| A | Série | Serie Exemple A | tvdb 990001, tt9990001, tmdb 880001 | — |
| A101/A102/A103 | Épisodes S01E01-03 | Ouverture Exemple / Episode Exemple de Test / Retour sur Terre Exemple | — | vus (A102 en 2023) |
| A201 | S02E01 | Saison Deux - Partie 1 | — | vu, PlayCount 3 |
| A202 | S02E02 | Serie.Exemple.A.S02E02.FRENCH.720p.WEB (nom = fichier) | — | aucune donnée |
| B | Série | Série Exemple B - Racines de Test | **pas de tvdb**, tt9990002, tmdb 880002 | — |
| B101 | S01E01 | Premier Exemple | — | vu (2 clés : GUID + tt) |
| B102 | S01E02 | Deuxième Exemple | — | en cours (2 clés) |
| B103 | S01E03 | Troisième Exemple | — | aucune donnée |

Médias supprimés (UserData détachées, `ItemId = 00000000-0000-0000-0000-000000000001`, `RetentionDate` rempli) :

| Code | Ce que c'était | CustomDataKey | Vu | Log VideoPlaybackStopped |
|---|---|---|---|---|
| D1 | film « Film Supprime Exemple 6 » | `24616b7b-…`, `900006`, `tt9900006` | oui | FR, ItemId |
| D2 | film « Film Supprime 7 - Version Longue » | GUID, `900007`, `tt9900007` | oui | **EN**, ItemId |
| D3 | film « Film Ancien Exemple 8 » (2023) | GUID, `900008`, `tt9900008` | oui | FR, **ItemId NULL** (rapprochement par titre seulement) |
| D4 | série C « Serie Supprimee C de Test » S01E01 | GUID, `990003001001`, `tt9990003001001` | oui | FR, ItemId |
| D5 | série C S01E02 « Exemple - Deuxieme Partie » | GUID, `990003001002`, `tt9990003001002` | oui | **EN**, ItemId |
| D6 | série A S01E04 (nom = fichier `…S01E04…`) | GUID, `990001001004`, `tt9990001001004` | oui, PlayCount 2 | FR, ItemId |
| D7 | série inconnue S02E03 | GUID, `dbeb8ed1-…-0e002003` (GUID série + SSSEEE) | oui | aucun |
| D8 | série B S01E04 « Quatrième Exemple » | GUID, `tt9990002001004` | oui | FR, ItemId |
| D9 | série C S01E03 | GUID, `990003001003`, `tt9990003001003` | oui | aucun |
| D10 | série C S01E04 | GUID, `990003001004`, `tt9990003001004` | oui | aucun ; **même LastPlayedDate que D9** (marquage groupé) |
| D11 | film « Film Abandonne Exemple 9 » | GUID, `900009`, `tt9900009` | **non** (position 1 200 s) | FR, ItemId |

La série C et les films D1-D3, D11 n'existent dans aucune table `BaseItems` ; leurs ids Tmdb/Imdb/Tvdb n'apparaissent
que dans `CustomDataKey`.

## Comptes attendus (pour les assertions de test)

### Tables
| | Lignes |
|---|---|
| Users | 1 |
| BaseItems | 22 (Movie 5, Series 2, Season 3, Episode 8, CollectionFolder 2, AggregateFolder 1, PLACEHOLDER 1) |
| BaseItemProviders | 31 (Imdb 15, Tvdb 9, Tmdb 7) |
| UserData | 59 (rattachées 28 sur 10 médias, détachées 31) |
| ActivityLogs | 40 |

### UserData rattachées (28 lignes)
* Films : 12 lignes / 4 films ; **vus : 3** (M1, M2, M5 — 9 lignes) ; en cours : 1 (M3).
* Épisodes : 16 lignes / 6 épisodes ; **vus : 5** (A101, A102, A103, A201, B101 — 14 lignes) ; en cours : 1 (B102).
* Après dédoublonnage par `ItemId` : 10 médias, **8 vus**. Sans dédoublonnage on compterait 23 « vus » : piège.

### UserData détachées (31 lignes)
* Films supprimés vus : 3 (D1, D2, D3) ; épisodes supprimés vus : 7 (D4, D5, D6, D7, D8, D9, D10).

### Export MediaTracker (jeu anonyme)

| Mesure | Avant R2 (`016339b`) | Après R2 |
|---|---|---|
| Lignes exportées (SQLite = ZIP) | 17 (20 userdata + 1 activity) | **17** (17 userdata, **0** activity) |
| `deleted_untitled_count` (guids) | 5 | **3** |
| Épisodes sans `series_title` | 2 | **2** (résolution à l'import TMDB) |
| Import à blanc (fixture + TMDB mocké) | séries « Inconnu » possibles | **0** « Inconnu », non créés si `/find` échoue |
* 31 lignes, **11 médias supprimés** (11 clés GUID), dont **10 vus** (28 lignes `Played=1`) et 1 non vu (D11, 3 lignes).
* Classes de clés : GUID 11, Tmdb film 4, Imdb film 4, Tvdb série+SSSEEE 5, Imdb série+SSSEEE 6, GUID série+SSSEEE 1.
* 10 valeurs distinctes de `LastPlayedDate` (D9 et D10 partagent la même).
* Films supprimés vus : 3 (D1, D2, D3) ; épisodes supprimés vus : 7 (D4, D5, D6, D7, D8, D9, D10).

### ActivityLogs (40 lignes)
| Type | Lignes | avec ItemId |
|---|---|---|
| VideoPlaybackStopped | 17 (FR 14, **EN 3**) | 15 (**2 NULL**) |
| VideoPlayback | 17 | 15 |
| SessionStarted | 2 | 0 |
| SessionEnded, PluginInstalled, UserPolicyUpdated, AuthenticationFailed | 1 chacun | 0 |

* `VideoPlaybackStopped` dont l'ItemId (32 hex) = un `BaseItems.Id` existant : **8** (M1, M2, M5, A101, A103, A201, B101, B102).
* dont l'ItemId remis en GUID = un `CustomDataKey` détaché : **7** (D1, D2, D4, D5, D6, D8, D11), dont **6 vus** (D11 non vu).
* Médias supprimés vus retrouvables : via log avec ItemId **6** ; via ids de fournisseur seulement **3** (D9, D10 par tvdb/imdb + SSSEEE, D3 par tmdb/imdb — D3 a aussi un log sans ItemId) ; **1** sans titre ni série identifiables (D7).
* Les `UserId` système (PluginInstalled, AuthenticationFailed) valent `00000000-0000-0000-0000-000000000000`.

## Pièges couverts
* `ActivityLogs.ItemId` = hex sans tirets ; `CustomDataKey` = GUID minuscules avec tirets ; `BaseItems.Id` = MAJUSCULES (SQLite) / minuscules (zip).
* Titres contenant ` - ` (M2, série B, A201, D2, D5), ` de ` (M3, A102, D4), ` sur ` (A103) : couper sur le **dernier** ` sur ` / ` on `, et ne pas couper aveuglément sur le premier ` - `.
* Apostrophe et `&` (M5) : en JSON ils sont échappés `\u0027`, `\u0026` ; caractères accentués en `\u00E9`, etc.
* Nom d'épisode égal à un nom de fichier contenant `SxxEyy` (A202, D6).
* Libellés anglais `has finished playing … on …` (3 lignes).
* Plusieurs lignes UserData par média (2 ou 3 clés) avec les mêmes `Played` / `LastPlayedDate`.
* Dates sans fraction (`2023-03-01 19:00:00`) et avec 7 décimales ; `IsMovie = 0` pour les films.
