# Profils de qualité

URL : `/reglages/qualite`

## Repli global

- **`allow_fallback`** : valeur par défaut pour les nouvelles fiches (le repli par fiche peut différer).
- **`profile_fallback_order`** : ordre des profils de qualité essayés après l’échec du profil principal (liste d’ids TMDB internes).

## Profils

Chaque profil définit : nom, langues ordonnées, résolution min/max, taille min/max (Mo), mots exclus dans le nom de fichier. Un profil peut être **défaut**. Wawacity, Comet et Hive utilisent le même profil pour une fiche, y compris lors d’un contrôle source immédiat.

Langues acceptées : VFF, VFQ, VF, MULTI, VOSTFR, VO (TRUEFRENCH enregistré comme VFF).

## Profils intégrés

Au premier démarrage, la base reçoit une liste de profils intégrés. Les mises à jour ultérieures **ajoutent** seulement un profil intégré encore absent (sans réécrire un profil que vous avez déjà modifié).

| Nom | Rôle |
| --- | --- |
| **VF/Multi 1080p** | Profil **défaut** sur une **nouvelle** base : min et max à **1080p** strict (migration initiale et défaut code). |
| 4K Multi | 4K, langues MULTI/VFF/VF |
| 720p VF | HD (720p) |
| VOSTFR 1080p | VOSTFR, plafond 1080p |
| Meilleure dispo | Pas de borne de résolution |
| **HDLight VF/Multi** | Cible **HDLight1080** uniquement (HDLight 1080p), langues VFF/VFQ/VF/MULTI |

Dans le formulaire, les résolutions proposées incluent **HDLight** (720p léger historique), **HD** (720p), **HDLight1080**, **1080p** et **4K**.

## Échelle des résolutions

Le moteur classe les résolutions du plus bas au plus haut : SD, HDLight, HD, **HDLight1080**, 1080p, 4K. Une sortie HDLight 1080p est donc **au-dessus** du HD 720p, mais **en dessous** du 1080p « plein » pour le profil par défaut (qui exige 1080p et rejette HDLight1080).
