# Structure réelle d'une base Jellyfin 10.11 et de son backup zip

Relevé fait le 31/10/2026 sur une vraie base (`jellyfin.db`, schéma EF Core) et sur le zip de backup
produit par la même instance. **Ce document ne contient aucune donnée réelle** : les titres, noms
d'utilisateur, appareils, chemins, GUID et IP sont masqués (`W` = un mot, `N` = un nombre,
`<user>` = un nom d'utilisateur, `<guid>` = un GUID).

Version : `manifest.json` du backup → `"ServerVersion":"12.3.0.0"`, `"BackupEngineVersion":"0.2.0"`
(c'est ce que rapporte cette build de Jellyfin 10.11). Dernière migration EF : `20261002210711_MakeOwnerIdIndexesPartial`.

---

## 1. Base SQLite (`jellyfin.db`)

### 1.1 Tables présentes

`AccessSchedules, ActivityLogs, AncestorIds, ApiKeys, AttachmentStreamInfos, BaseItemImageInfos,
BaseItemMetadataFields, BaseItemProviders, BaseItemTrailerTypes, BaseItems, Chapters,
CustomItemDisplayPreferences, DeviceOptions, Devices, DisplayPreferences, HomeSection, ImageInfos,
ItemDisplayPreferences, ItemValues, ItemValuesMap, KeyframeData, LinkedChildren, MediaSegments,
MediaStreamInfos, PeopleBaseItemMap, Peoples, Permissions, Preferences, TrickplayInfos, UserData, Users,
__EFMigrationsHistory, __EFMigrationsLock, sqlite_sequence, sqlite_stat1`

Note : `ActivityLogs` est **dans `jellyfin.db`** (en 10.11 il n'y a plus de `activitylog.db` séparée).

### 1.2 Colonnes utiles pour l'import d'historique

| Table | Colonnes utiles |
|---|---|
| Users | `Id`, `Username`, `NormalizedUsername` |
| BaseItems | `Id`, `Type`, `Name`, `OriginalTitle`, `ProductionYear`, `PremiereDate`, `RunTimeTicks`, `ParentId`, `SeriesId`, `SeasonId`, `SeriesName`, `SeasonName`, `IndexNumber` (n° d'épisode / de saison), `ParentIndexNumber` (n° de saison de l'épisode), `PresentationUniqueKey`, `SeriesPresentationUniqueKey`, `TopParentId`, `IsVirtualItem`, `Path` |
| BaseItemProviders | `ItemId`, `ProviderId`, `ProviderValue` |
| UserData | `ItemId`, `UserId`, `CustomDataKey`, `Played`, `PlayCount`, `LastPlayedDate`, `PlaybackPositionTicks`, `IsFavorite`, `RetentionDate` |
| ActivityLogs | `Id`, `Name`, `Type`, `UserId`, `ItemId`, `DateCreated` |

Attention : `BaseItems.IsMovie` vaut **0 pour les films** (et `IsSeries` 0 pour les séries). Il faut se fier à `Type`.

### 1.3 DDL exact (copié de `sqlite_master`, index compris)

### Users

```sql
CREATE TABLE "Users" (
    "Id" TEXT NOT NULL CONSTRAINT "PK_Users" PRIMARY KEY,
    "AudioLanguagePreference" TEXT NULL,
    "AuthenticationProviderId" TEXT NOT NULL,
    "CastReceiverId" TEXT NULL,
    "DisplayCollectionsView" INTEGER NOT NULL,
    "DisplayMissingEpisodes" INTEGER NOT NULL,
    "EnableAutoLogin" INTEGER NOT NULL,
    "EnableLocalPassword" INTEGER NOT NULL,
    "EnableNextEpisodeAutoPlay" INTEGER NOT NULL,
    "EnableUserPreferenceAccess" INTEGER NOT NULL,
    "HidePlayedInLatest" INTEGER NOT NULL,
    "InternalId" INTEGER NOT NULL,
    "InvalidLoginAttemptCount" INTEGER NOT NULL,
    "LastActivityDate" TEXT NULL,
    "LastLoginDate" TEXT NULL,
    "LoginAttemptsBeforeLockout" INTEGER NULL,
    "MaxActiveSessions" INTEGER NOT NULL,
    "MaxParentalRatingScore" INTEGER NULL,
    "MustUpdatePassword" INTEGER NOT NULL,
    "Password" TEXT NULL,
    "PasswordResetProviderId" TEXT NOT NULL,
    "PlayDefaultAudioTrack" INTEGER NOT NULL,
    "RememberAudioSelections" INTEGER NOT NULL,
    "RememberSubtitleSelections" INTEGER NOT NULL,
    "RemoteClientBitrateLimit" INTEGER NULL,
    "RowVersion" INTEGER NOT NULL,
    "SubtitleLanguagePreference" TEXT NULL,
    "SubtitleMode" INTEGER NOT NULL,
    "SyncPlayAccess" INTEGER NOT NULL,
    "Username" TEXT NOT NULL
, "MaxParentalRatingSubScore" INTEGER NULL, "NormalizedUsername" TEXT NOT NULL DEFAULT '');
CREATE UNIQUE INDEX "IX_Users_Username" ON "Users" ("Username");
CREATE UNIQUE INDEX "IX_Users_NormalizedUsername" ON "Users" ("NormalizedUsername");
```

### UserData

```sql
CREATE TABLE "UserData" (
    "ItemId" TEXT NOT NULL,
    "UserId" TEXT NOT NULL,
    "CustomDataKey" TEXT NOT NULL,
    "AudioStreamIndex" INTEGER NULL,
    "IsFavorite" INTEGER NOT NULL,
    "LastPlayedDate" TEXT NULL,
    "Likes" INTEGER NULL,
    "PlayCount" INTEGER NOT NULL,
    "PlaybackPositionTicks" INTEGER NOT NULL,
    "Played" INTEGER NOT NULL,
    "Rating" REAL NULL,
    "SubtitleStreamIndex" INTEGER NULL, "RetentionDate" TEXT NULL,
    CONSTRAINT "PK_UserData" PRIMARY KEY ("ItemId", "UserId", "CustomDataKey"),
    CONSTRAINT "FK_UserData_BaseItems_ItemId" FOREIGN KEY ("ItemId") REFERENCES "BaseItems" ("Id") ON DELETE CASCADE,
    CONSTRAINT "FK_UserData_Users_UserId" FOREIGN KEY ("UserId") REFERENCES "Users" ("Id") ON DELETE CASCADE
);
CREATE INDEX "IX_UserData_ItemId_UserId_IsFavorite" ON "UserData" ("ItemId", "UserId", "IsFavorite");
CREATE INDEX "IX_UserData_ItemId_UserId_LastPlayedDate" ON "UserData" ("ItemId", "UserId", "LastPlayedDate");
CREATE INDEX "IX_UserData_ItemId_UserId_PlaybackPositionTicks" ON "UserData" ("ItemId", "UserId", "PlaybackPositionTicks");
CREATE INDEX "IX_UserData_ItemId_UserId_Played" ON "UserData" ("ItemId", "UserId", "Played");
CREATE INDEX "IX_UserData_UserId_ItemId_LastPlayedDate" ON "UserData" ("UserId", "ItemId", "LastPlayedDate");
CREATE INDEX "IX_UserData_UserId_IsFavorite_ItemId" ON "UserData" ("UserId", "IsFavorite", "ItemId");
CREATE INDEX "IX_UserData_UserId_Played_ItemId" ON "UserData" ("UserId", "Played", "ItemId");
```

### BaseItems

```sql
CREATE TABLE "BaseItems" (
    "Id" TEXT NOT NULL CONSTRAINT "PK_BaseItems" PRIMARY KEY,
    "Album" TEXT NULL,
    "AlbumArtists" TEXT NULL,
    "Artists" TEXT NULL,
    "Audio" INTEGER NULL,
    "ChannelId" TEXT NULL,
    "CleanName" TEXT NULL,
    "CommunityRating" REAL NULL,
    "CriticRating" REAL NULL,
    "CustomRating" TEXT NULL,
    "Data" TEXT NULL,
    "DateCreated" TEXT NULL,
    "DateLastMediaAdded" TEXT NULL,
    "DateLastRefreshed" TEXT NULL,
    "DateLastSaved" TEXT NULL,
    "DateModified" TEXT NULL,
    "EndDate" TEXT NULL,
    "EpisodeTitle" TEXT NULL,
    "ExternalId" TEXT NULL,
    "ExternalSeriesId" TEXT NULL,
    "ExternalServiceId" TEXT NULL,
    "ExtraType" INTEGER NULL,
    "ForcedSortName" TEXT NULL,
    "Genres" TEXT NULL,
    "Height" INTEGER NULL,
    "IndexNumber" INTEGER NULL,
    "InheritedParentalRatingSubValue" INTEGER NULL,
    "InheritedParentalRatingValue" INTEGER NULL,
    "IsFolder" INTEGER NOT NULL,
    "IsInMixedFolder" INTEGER NOT NULL,
    "IsLocked" INTEGER NOT NULL,
    "IsMovie" INTEGER NOT NULL,
    "IsRepeat" INTEGER NOT NULL,
    "IsSeries" INTEGER NOT NULL,
    "IsVirtualItem" INTEGER NOT NULL,
    "LUFS" REAL NULL,
    "MediaType" TEXT NULL,
    "Name" TEXT NULL,
    "NormalizationGain" REAL NULL,
    "OfficialRating" TEXT NULL,
    "OriginalLanguage" TEXT NULL,
    "OriginalTitle" TEXT NULL,
    "Overview" TEXT NULL,
    "OwnerId" TEXT NULL,
    "ParentId" TEXT NULL,
    "ParentIndexNumber" INTEGER NULL,
    "Path" TEXT NULL,
    "PreferredMetadataCountryCode" TEXT NULL,
    "PreferredMetadataLanguage" TEXT NULL,
    "PremiereDate" TEXT NULL,
    "PresentationUniqueKey" TEXT NULL,
    "PrimaryVersionId" TEXT NULL,
    "ProductionLocations" TEXT NULL,
    "ProductionYear" INTEGER NULL,
    "RunTimeTicks" INTEGER NULL,
    "SeasonId" TEXT NULL,
    "SeasonName" TEXT NULL,
    "SeriesId" TEXT NULL,
    "SeriesName" TEXT NULL,
    "SeriesPresentationUniqueKey" TEXT NULL,
    "ShowId" TEXT NULL,
    "Size" INTEGER NULL,
    "SortName" TEXT NULL,
    "StartDate" TEXT NULL,
    "Studios" TEXT NULL,
    "Tagline" TEXT NULL,
    "Tags" TEXT NULL,
    "TopParentId" TEXT NULL,
    "TotalBitrate" INTEGER NULL,
    "Type" TEXT NOT NULL,
    "UnratedType" TEXT NULL,
    "Width" INTEGER NULL,
    CONSTRAINT "FK_BaseItems_BaseItems_OwnerId" FOREIGN KEY ("OwnerId") REFERENCES "BaseItems" ("Id"),
    CONSTRAINT "FK_BaseItems_BaseItems_ParentId" FOREIGN KEY ("ParentId") REFERENCES "BaseItems" ("Id") ON DELETE CASCADE
);
CREATE INDEX "IX_BaseItems_IsFolder_TopParentId_IsVirtualItem_PresentationUniqueKey_DateCreated" ON "BaseItems" ("IsFolder", "TopParentId", "IsVirtualItem", "PresentationUniqueKey", "DateCreated");
CREATE INDEX "IX_BaseItems_MediaType_TopParentId_IsVirtualItem_PresentationUniqueKey" ON "BaseItems" ("MediaType", "TopParentId", "IsVirtualItem", "PresentationUniqueKey");
CREATE INDEX "IX_BaseItems_ParentId" ON "BaseItems" ("ParentId");
CREATE INDEX "IX_BaseItems_Path" ON "BaseItems" ("Path");
CREATE INDEX "IX_BaseItems_PresentationUniqueKey" ON "BaseItems" ("PresentationUniqueKey");
CREATE INDEX "IX_BaseItems_TopParentId_Id" ON "BaseItems" ("TopParentId", "Id");
CREATE INDEX "IX_BaseItems_TopParentId_IsFolder_IsVirtualItem_DateCreated" ON "BaseItems" ("TopParentId", "IsFolder", "IsVirtualItem", "DateCreated");
CREATE INDEX "IX_BaseItems_TopParentId_MediaType_IsVirtualItem_DateCreated" ON "BaseItems" ("TopParentId", "MediaType", "IsVirtualItem", "DateCreated");
CREATE INDEX "IX_BaseItems_TopParentId_Type_IsVirtualItem_DateCreated" ON "BaseItems" ("TopParentId", "Type", "IsVirtualItem", "DateCreated");
CREATE INDEX "IX_BaseItems_Type_SeriesPresentationUniqueKey_IsFolder_IsVirtualItem" ON "BaseItems" ("Type", "SeriesPresentationUniqueKey", "IsFolder", "IsVirtualItem");
CREATE INDEX "IX_BaseItems_Type_SeriesPresentationUniqueKey_PresentationUniqueKey_SortName" ON "BaseItems" ("Type", "SeriesPresentationUniqueKey", "PresentationUniqueKey", "SortName");
CREATE INDEX "IX_BaseItems_Type_TopParentId_Id" ON "BaseItems" ("Type", "TopParentId", "Id");
CREATE INDEX "IX_BaseItems_Type_TopParentId_IsVirtualItem_PresentationUniqueKey_DateCreated" ON "BaseItems" ("Type", "TopParentId", "IsVirtualItem", "PresentationUniqueKey", "DateCreated");
CREATE INDEX "IX_BaseItems_Type_TopParentId_PresentationUniqueKey" ON "BaseItems" ("Type", "TopParentId", "PresentationUniqueKey");
CREATE INDEX "IX_BaseItems_Type_TopParentId_StartDate" ON "BaseItems" ("Type", "TopParentId", "StartDate");
CREATE INDEX "IX_BaseItems_Name" ON "BaseItems" ("Name");
CREATE INDEX "IX_BaseItems_SeasonId" ON "BaseItems" ("SeasonId");
CREATE INDEX "IX_BaseItems_SeriesId" ON "BaseItems" ("SeriesId");
CREATE INDEX "IX_BaseItems_SeriesName" ON "BaseItems" ("SeriesName");
CREATE INDEX "IX_BaseItems_Type_SeriesPresentationUniqueKey_ParentIndexNumber_IndexNumber" ON "BaseItems" ("Type", "SeriesPresentationUniqueKey", "ParentIndexNumber", "IndexNumber");
CREATE INDEX "IX_BaseItems_Type_TopParentId_SortName" ON "BaseItems" ("Type", "TopParentId", "SortName");
CREATE INDEX "IX_BaseItems_Type_CleanName" ON "BaseItems" ("Type", "CleanName");
CREATE INDEX "IX_BaseItems_TopParentId_Type_IsVirtualItem" ON "BaseItems" ("TopParentId", "Type", "IsVirtualItem") WHERE "PrimaryVersionId" IS NULL AND ("OwnerId" IS NULL OR "ExtraType" IS NOT NULL);
CREATE INDEX "IX_BaseItems_PrimaryVersionId" ON "BaseItems" ("PrimaryVersionId") WHERE "PrimaryVersionId" IS NOT NULL;
CREATE INDEX "IX_BaseItems_ExtraType_OwnerId" ON "BaseItems" ("ExtraType", "OwnerId") WHERE "ExtraType" IS NOT NULL;
CREATE INDEX "IX_BaseItems_OwnerId" ON "BaseItems" ("OwnerId") WHERE "OwnerId" IS NOT NULL;
```

### BaseItemProviders

```sql
CREATE TABLE "BaseItemProviders" (
    "ItemId" TEXT NOT NULL,
    "ProviderId" TEXT NOT NULL,
    "ProviderValue" TEXT NOT NULL,
    CONSTRAINT "PK_BaseItemProviders" PRIMARY KEY ("ItemId", "ProviderId"),
    CONSTRAINT "FK_BaseItemProviders_BaseItems_ItemId" FOREIGN KEY ("ItemId") REFERENCES "BaseItems" ("Id") ON DELETE CASCADE
);
CREATE INDEX "IX_BaseItemProviders_ProviderId_ItemId_ProviderValue" ON "BaseItemProviders" ("ProviderId", "ItemId", "ProviderValue");
```

### ActivityLogs

```sql
CREATE TABLE "ActivityLogs" (
    "Id" INTEGER NOT NULL CONSTRAINT "PK_ActivityLogs" PRIMARY KEY AUTOINCREMENT,
    "Name" TEXT NOT NULL,
    "Overview" TEXT NULL,
    "ShortOverview" TEXT NULL,
    "Type" TEXT NOT NULL,
    "UserId" TEXT NOT NULL,
    "ItemId" TEXT NULL,
    "DateCreated" TEXT NOT NULL,
    "LogSeverity" INTEGER NOT NULL,
    "RowVersion" INTEGER NOT NULL
);
CREATE INDEX "IX_ActivityLogs_DateCreated" ON "ActivityLogs" ("DateCreated");
```
### 1.4 Formats de valeurs (SQLite)

| Élément | Format observé |
|---|---|
| GUID dans les colonnes `Id`, `ItemId`, `UserId`, `ParentId`, `SeriesId`, `SeasonId`, `TopParentId`, `ChannelId` | **MAJUSCULES avec tirets** : `XXXXXXXX-XXXX-XXXX-XXXX-XXXXXXXXXXXX` |
| `ActivityLogs.UserId` | MAJUSCULES avec tirets ; `00000000-0000-0000-0000-000000000000` pour les événements système (plugins, échecs de connexion, tâches) |
| `ActivityLogs.ItemId` | **32 caractères hexadécimaux minuscules sans tirets** (`N` format .NET), ou NULL |
| `UserData.CustomDataKey` | voir 1.6 (GUID **minuscules** avec tirets, ou clé de fournisseur) |
| `BaseItems.PresentationUniqueKey` | film / épisode : GUID en hex minuscules sans tirets (= Id) ; série : `<tvdb-ou-tmdb>-fr-<hex32>` ; saison : `<clé série>-NNN` (ex. `-002`) |
| `BaseItems.SeriesPresentationUniqueKey` | clé de la série (épisodes et saisons) |
| Dates (`DateCreated`, `LastPlayedDate`, `RetentionDate`, `DateLastSaved`…) | `YYYY-MM-DD HH:MM:SS.fffffff`, UTC sans suffixe, **zéros de fin des fractions supprimés** (donc 0 à 7 décimales, et pas de point quand la fraction vaut 0) |
| `PremiereDate` | `YYYY-MM-DD HH:MM:SS` (souvent `22:00:00` ou `23:00:00` = minuit heure de Paris convertie en UTC, ou `00:00:00`) |
| Booléens | INTEGER 0/1 |
| `RunTimeTicks`, `PlaybackPositionTicks` | ticks .NET (1 s = 10 000 000) |
| `BaseItems.Data` | JSON compact (clés `AdditionalParts`, `LocalAlternateVersions`, `SubtitleFiles`, `VideoType`, `Size`, `IsHD`, `Width`, `Height`, `DateLastSaved`, `RemoteTrailers`…) |
| `Genres`, `Tags`, `Studios` | chaînes séparées par `|` |

**Valeurs de `BaseItems.Type`** (noms .NET complets) :
`MediaBrowser.Controller.Entities.Movies.Movie`, `MediaBrowser.Controller.Entities.TV.Series`,
`MediaBrowser.Controller.Entities.TV.Season`, `MediaBrowser.Controller.Entities.TV.Episode`,
`MediaBrowser.Controller.Entities.Person`, `MediaBrowser.Controller.Entities.Genre`,
`MediaBrowser.Controller.Entities.Studio`, `MediaBrowser.Controller.Entities.Folder`,
`MediaBrowser.Controller.Entities.CollectionFolder`, `MediaBrowser.Controller.Entities.AggregateFolder`,
`MediaBrowser.Controller.Entities.UserRootFolder`, `MediaBrowser.Controller.Entities.UserView`,
`Emby.Server.Implementations.Playlists.PlaylistsFolder`, et **`PLACEHOLDER`** (une seule ligne).

`MediaType` : `Video` (films, épisodes), `Unknown` (dossiers, séries, saisons), NULL.
`UnratedType` : `Movie` (films), `Series` (séries, saisons, épisodes), `Other`.

**Ligne placeholder** : `Id = 00000000-0000-0000-0000-000000000001`, `Type = 'PLACEHOLDER'`,
`Name = 'This is a placeholder item for UserData that has been detached from its original item'`,
`Path` NULL, tous les autres champs NULL ou 0.

**`BaseItemProviders.ProviderId`** : `Tmdb` (chiffres), `Imdb` (`ttNNNNNNN` pour un titre, `nmNNNNNNN` pour une personne),
`Tvdb` (chiffres), `TvMaze` (chiffres), `TmdbCollection` (chiffres), `TvRage`.

### 1.5 UserData : rattachée ou détachée

* **Rattachée** : `ItemId` = un vrai `BaseItems.Id`, `RetentionDate` NULL.
* **Détachée** (média supprimé de la bibliothèque) : `ItemId = 00000000-0000-0000-0000-000000000001`
  (la ligne PLACEHOLDER), `RetentionDate` rempli (date du détachement), et `CustomDataKey` garde l'ancienne clé.
  `CustomDataKey` ne correspond **jamais** à un `BaseItems.Id` existant.

### 1.6 `CustomDataKey` : il y a **plusieurs lignes par média**

Contrairement à ce qu'on pensait (« toujours un GUID de 36 caractères »), Jellyfin écrit **une ligne UserData
par clé de données utilisateur** du média, avec les mêmes `Played` / `PlayCount` / `LastPlayedDate`.
La clé primaire `(ItemId, UserId, CustomDataKey)` le permet. Formes observées :

| Forme | Exemple masqué | Signification |
|---|---|---|
| GUID minuscules avec tirets (36 car.) | `xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx` | `Id` du média (en minuscules) — c'est cette clé qui égale `ActivityLogs.ItemId` remis au format avec tirets |
| chiffres (1 à 7) | `NNNNNN` | film : id **Tmdb** |
| `tt` + 7-8 chiffres | `ttNNNNNNN` | film : id **Imdb** |
| 11-12 chiffres | `NNNNNN` + `SSS` + `EEE` | épisode : id **Tvdb de la série** + saison sur 3 chiffres + épisode sur 3 chiffres |
| `tt` + 13-14 chiffres | `ttNNNNNNNN` + `SSS` + `EEE` | épisode : id **Imdb de la série** + saison + épisode |
| GUID + 6 chiffres (42 car.) | `xxxxxxxx-…-xxxxxxxxxxxx` + `SSSEEE` | épisode d'une série sans id Tvdb/Imdb : GUID de la **série** + saison + épisode |
| `Person-<nom>` | `Person-W W` | personne (favori) |

Combinaisons par média (lignes détachées regroupées par `LastPlayedDate` identique) :
film = {GUID, Tmdb, Imdb} ; épisode = {GUID, Tvdb+SSSEEE, Imdb+SSSEEE} ou {GUID, Imdb+SSSEEE} ou {GUID, GUIDsérie+SSSEEE}.
Les lignes d'un même média partagent `LastPlayedDate` ; ce n'est pas une clé fiable à 100 % (un « marquer comme vu »
groupé donne la même date à plusieurs médias : 50 groupes sur 2 700 contiennent plusieurs GUID).

Conséquence pour l'import : un épisode supprimé est souvent identifiable **sans ActivityLogs** grâce à sa clé
`<tvdb série>SSSEEE` ou `<imdb série>SSSEEE`, et un film supprimé grâce à ses clés Tmdb / Imdb.
Attention à l'ambiguïté : une clé de 7 chiffres peut être un Tmdb de film à 7 chiffres ou un Tvdb à 1 chiffre + SSSEEE
(en pratique, dans cette base, ce sont des films).

### 1.7 ActivityLogs

**Valeurs de `Type` observées** (nombre de lignes) :
`SessionStarted` (2 056), `VideoPlayback` (3 204), `VideoPlaybackStopped` (3 160), `SessionEnded` (884),
`AuthenticationSucceeded` (27), `PluginInstalled` (27), `UserDownloadingContent` (11), `PluginUninstalled` (3),
`AuthenticationFailed` (2), `TaskFailed` (2), `UserPasswordChanged` (2), `PluginUpdateInstalled` (1), `UserPolicyUpdated` (1).

**Gabarits de `Name`** (instance en français ; 100 % des lignes `VideoPlaybackStopped` suivent ce gabarit) :

| Type | Gabarit |
|---|---|
| VideoPlaybackStopped (film) | `<user> vient d'arrêter la lecture de W… sur <Appareil>` |
| VideoPlaybackStopped (épisode) | `<user> vient d'arrêter la lecture de <Série W…> - <Épisode W…> sur <Appareil>` |
| VideoPlayback | `<user> est en train de lire W… sur <Appareil>` (même découpage) |
| SessionStarted | `<user> s'est connecté depuis W` |
| SessionEnded | `<user> s'est déconnecté depuis W W` |
| UserDownloadingContent | `<user> est en train de télécharger W N.N.N.N` |
| AuthenticationFailed | `Échec de connexion depuis W` |
| PluginInstalled | `W a été installé` |
| UserPolicyUpdated | `La politique de l'utilisateur a été mise à jour pour <user>` |
| UserPasswordChanged | `Le mot de passe de l'utilisateur <user> a été modifié` |

Équivalents anglais (chaînes Jellyfin, absents de cette base mais possibles selon la langue du serveur) :
`<user> has finished playing W… on <Appareil>` et `<user> is playing W… on <Appareil>`.

Remarques sur les 3 160 `VideoPlaybackStopped` :
* le gabarit **ne contient pas `a fini de lire`** ni de `SxxEyy` ajouté par Jellyfin. L'épisode est écrit `Série - NomÉpisode` ;
* 2 862 noms contiennent un ` - `, 45 en contiennent **deux** (titre de série ou d'épisode qui contient lui-même ` - `), 253 aucun (films) ;
* 20 contiennent **deux fois ` sur `** (titre contenant « sur ») : couper sur le **dernier** ` sur ` ;
* 294 contiennent ` de ` dans le titre ;
* 1 126 contiennent un motif `SxxEyy` parce que le **nom de l'épisode est un nom de fichier** (`W.W.SxxEyy.W.720p.W`) ;
* 15 appareils distincts, de forme `W`, `W W`, `W (N.N.N.N)` (client + adresse IP) ou `W ([W])` ;
* `ShortOverview` et `Overview` sont NULL pour les événements de lecture ; `ShortOverview` vaut `Adresse IP : N.N.N.N`
  (avec espace insécable U+00A0 avant `:` jusqu'en 2024, espace normale ensuite) pour les sessions ;
* `LogSeverity` 0 ou 2 (4 pour les échecs) ; `RowVersion` 0.

**`ItemId` dans ActivityLogs** : NULL avant le 12/05/2024 (2 172 `VideoPlaybackStopped`), rempli depuis (988 lignes,
763 ItemId distincts). Format 32 hex minuscules. Après remise des tirets :
* 738 ItemId distincts = `CustomDataKey` (forme GUID) d'une ligne UserData **détachée** ;
* 25 = `Id` d'un `BaseItems` encore présent ;
* 0 ne correspond à rien.
Sur les 2 824 clés GUID détachées marquées vues, 727 sont retrouvables via un `VideoPlaybackStopped`.

### 1.8 Statistiques (comptes uniquement)

| Table | Lignes |
|---|---|
| Users | 1 |
| BaseItems | 8 179 (Person 7 737, Episode 229, Studio 123, Season 29, Genre 25, Series 20, Movie 7, Folder 2, CollectionFolder 2, AggregateFolder 1, UserRootFolder 1, UserView 1, PlaylistsFolder 1, PLACEHOLDER 1) |
| BaseItemProviders | 8 197 (Tmdb 5 861, Tvdb 1 188, TvMaze 860, Imdb 272, TmdbCollection 15, TvRage 1) |
| UserData | 7 573 |
| ActivityLogs | 9 380 (du 01/05/2020 au 31/10/2026) |

UserData détaillée :

| | Lignes | Vues (`Played=1`) |
|---|---|---|
| Rattachées (`ItemId` réel) | 182 lignes pour 61 médias | 159 (53 épisodes × 3 clés) ; films : 9 lignes (3 films), 0 vu ; 1 personne (2 lignes) |
| Détachées (placeholder) | 7 391 | 7 327 ; `RetentionDate` rempli sur les 7 391 |

Clés des lignes détachées : GUID 2 856 (2 824 vues), Imdb série+SSSEEE 2 305 (2 290 vues),
Tvdb série+SSSEEE 1 644 (1 634 vues), GUID série+SSSEEE 240 (239 vues), Tmdb film 173 (170 vues), Imdb film 173 (170 vues).
2 700 valeurs distinctes (NULL compris) de `LastPlayedDate` parmi les lignes détachées.

---

## 2. Backup zip (Jellyfin 10.11, « Sauvegardes » du tableau de bord)

### 2.1 Arborescence (74 entrées, aucune entrée de dossier, séparateur `/`, Deflate)

```
Database/AccessSchedules.json
Database/ActivityLogs.json
Database/ApiKeys.json
Database/Devices.json
Database/DeviceOptions.json
Database/DisplayPreferences.json
Database/ImageInfos.json
Database/ItemDisplayPreferences.json
Database/CustomItemDisplayPreferences.json
Database/Permissions.json
Database/Preferences.json
Database/Users.json
Database/TrickplayInfos.json
Database/MediaSegments.json
Database/UserData.json
Database/AncestorIds.json
Database/AttachmentStreamInfos.json
Database/BaseItems.json
Database/Chapters.json
Database/ItemValues.json
Database/ItemValuesMap.json
Database/MediaStreamInfos.json
Database/Peoples.json
Database/PeopleBaseItemMap.json
Database/LinkedChildren.json
Database/BaseItemProviders.json
Database/BaseItemImageInfos.json
Database/BaseItemMetadataFields.json
Database/BaseItemTrailerTypes.json
Database/KeyframeData.json
Database/HistoryRow.json            (= __EFMigrationsHistory : MigrationId, ProductVersion)
Config/playback_reporting.xml
Config/database.xml
Config/network.xml
Config/system.xml
Config/encoding.xml
Config/logging.default.json
Config/users/<user>/policy.xml
Config/users/<user>/profile.jpg
Config/users/<user2>/policy.xml
Root/.jellyfin-root
Root/default/<bibliothèque films>/options.xml
Root/default/<bibliothèque films>/movies.mblink
Root/default/<bibliothèque films>/movies.collection
Root/default/<bibliothèque séries>/tvshows.mblink
Root/default/<bibliothèque séries>/options.xml
Root/default/<bibliothèque séries>/tvshows.collection
Data/ScheduledTasks/<guid>.js       (×26)
manifest.json                       (dernière entrée)
```

Il n'y a pas de table `HomeSection`, `__EFMigrationsLock`, `sqlite_sequence` ni `sqlite_stat1` dans le zip ;
`HistoryRow.json` remplace `__EFMigrationsHistory`. Pas de dossier `Metadata/`, `Trickplay/` ni `Subtitles/`
(options désactivées dans le manifest).

### 2.2 `manifest.json` (présent)

```json
{"ServerVersion":"12.3.0.0","BackupEngineVersion":"0.2.0","DateCreated":"YYYY-MM-DDTHH:MM:SS.fffffff+00:00",
 "DatabaseTables":["DbSet\u00601", … ×30, "HistoryRow"],
 "Options":{"Metadata":false,"Trickplay":false,"Subtitles":false,"Database":true}}
```

`DatabaseTables` contient des noms de type génériques (`DbSet`1`) et non les noms de tables : **inutilisable pour lister
les tables**, il faut lister `Database/*.json` dans le zip.

### 2.3 Format des fichiers `Database/*.json`

* UTF-8 **sans BOM**, sur **une seule ligne**, compact, **tableau JSON** de premier niveau (`[ {...}, {...} ]`).
* Une entrée = une ligne de la table, clés = noms de colonnes EF (même casse), plus les **propriétés de navigation
  EF** à `null` (ou `[]` pour Users).
* Échappement System.Text.Json : tout caractère non ASCII en `\uXXXX` **majuscules** (`\u00E9`), et aussi
  `'` → `\u0027`, `"` → `\u0022`, `&` → `\u0026`, `<` → `\u003C`, `>` → `\u003E`, `+` → `\u002B`, `` ` `` → `\u0060`.
  Un parseur JSON standard gère tout cela, mais pas un `grep` sur le texte brut.
* GUID : **minuscules avec tirets** partout (alors qu'ils sont en MAJUSCULES dans SQLite) — sauf `ActivityLogs.ItemId`
  qui reste en 32 hex minuscules sans tirets, et `CustomDataKey` qui est recopié tel quel.
* Dates : ISO 8601 UTC `YYYY-MM-DDTHH:MM:SS[.f…]Z`, zéros de fin supprimés (0 à 7 décimales).
* Booléens : `true`/`false` (0/1 dans SQLite). Nombres : entiers ou flottants JSON.

#### Users.json — tableau de 1 objet, clés dans cet ordre
`Id` (guid), `Username`, `NormalizedUsername`, `Password` (`$PBKDF2-SHA512$iterations=N$HEX$HEX`), `MustUpdatePassword` (bool),
`AudioLanguagePreference`, `AuthenticationProviderId`, `PasswordResetProviderId`, `InvalidLoginAttemptCount`, `LastActivityDate`,
`LastLoginDate`, `LoginAttemptsBeforeLockout`, `MaxActiveSessions`, `SubtitleMode`, `PlayDefaultAudioTrack`, `SubtitleLanguagePreference`,
`DisplayMissingEpisodes`, `DisplayCollectionsView`, `EnableLocalPassword`, `HidePlayedInLatest`, `RememberAudioSelections`,
`RememberSubtitleSelections`, `EnableNextEpisodeAutoPlay`, `EnableAutoLogin`, `EnableUserPreferenceAccess`, `MaxParentalRatingScore`,
`MaxParentalRatingSubScore`, `RemoteClientBitrateLimit`, `InternalId`, `ProfileImage` (null), `DisplayPreferences` ([]), `SyncPlayAccess`,
`CastReceiverId`, `RowVersion`, `AccessSchedules` ([]), `ItemDisplayPreferences` ([]), `Permissions` ([]), `Preferences` ([]).
(Les listes de navigation sont vides : les permissions/préférences sont dans leurs propres fichiers.)

#### UserData.json — tableau de 7 573 objets
`CustomDataKey` (str), `Rating` (null), `PlaybackPositionTicks` (int), `PlayCount` (int), `IsFavorite` (bool), `LastPlayedDate` (date|null),
`Played` (bool), `AudioStreamIndex` (int|null), `SubtitleStreamIndex` (int|null), `Likes` (null), `RetentionDate` (date|null),
`ItemId` (guid minuscule, `00000000-0000-0000-0000-000000000001` pour les détachées : 7 391), `Item` (null), `UserId` (guid), `User` (null).
→ **le zip contient bien `UserData` avec `CustomDataKey` et `RetentionDate`**, mêmes lignes que SQLite.

#### BaseItems.json — tableau de 8 179 objets
Toutes les colonnes SQLite (bool en `true`/`false`, GUID minuscules, dates ISO Z, `Data` reste une **chaîne** JSON) +
navigations à `null` : `Owner`, `Extras`, `DirectParent`, `Peoples`, `UserData`, `ItemValues`, `MediaStreams`, `Chapters`, `Provider`,
`Parents`, `Children`, `DirectChildren`, `LockedFields`, `TrailerTypes`, `Images`, `LinkedChildEntities`, `LinkedChildOfEntities`.
La séquence des clés diffère du DDL (commence par `Id`, `Type`, `Data`, `Path`, `StartDate`, `EndDate`, `ChannelId`, `IsMovie`…).
La ligne `PLACEHOLDER` y figure. `BaseItems.UserData` et `BaseItems.Provider` sont `null` : il faut joindre les fichiers.

#### BaseItemProviders.json — tableau de 8 197 objets
`ItemId` (guid minuscule), `Item` (null), `ProviderId` (str), `ProviderValue` (str).

#### ActivityLogs.json — tableau de 9 380 objets
`Id` (int), `Name` (str), `Overview` (str|null), `ShortOverview` (str|null), `Type` (str), `UserId` (guid minuscule),
`ItemId` (32 hex minuscules|null), `DateCreated` (date ISO Z), `LogSeverity` (int), `RowVersion` (int).
→ **le zip contient bien les ActivityLogs**, mêmes 9 380 lignes que SQLite, même texte de `Name`.

### 2.4 Correspondance zip ↔ SQLite

| SQLite | Zip | Différences |
|---|---|---|
| table `X` | `Database/X.json` | même nombre de lignes, mêmes noms de colonnes |
| GUID `XXXXXXXX-…` MAJUSCULES | `xxxxxxxx-…` minuscules | normaliser en minuscules pour joindre |
| `ActivityLogs.ItemId` 32 hex | identique | — |
| `UserData.CustomDataKey` | identique | — |
| dates `YYYY-MM-DD HH:MM:SS.f` | `YYYY-MM-DDTHH:MM:SS.fZ` | les deux sont en UTC |
| booléens 0/1 | `true`/`false` | |
| — | propriétés de navigation `null` / `[]` | à ignorer |
| `__EFMigrationsHistory` | `HistoryRow.json` | |
| `HomeSection`, `__EFMigrationsLock`, `sqlite_*` | absents | |
| — | `Config/`, `Root/`, `Data/ScheduledTasks/`, `manifest.json` | fichiers non-base |

Pour MediaTracker, le zip est donc **équivalent à `jellyfin.db`** pour l'import : mêmes données utilisateur
(UserData détachées comprises) et même journal d'activité.
