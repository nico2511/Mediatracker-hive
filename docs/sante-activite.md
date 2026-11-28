# Santé › Activité (carte réseau)

L'onglet **Activité** de la page **Santé** affiche une carte de métro en **lecture seule** : trois niveaux de zoom (12 lignes, 33 stations visibles, mécanismes de la station Finalisation), alimentée par la collecte `task_run` (interrupteur **Réglages › Application**).

## Données

- Fenêtre affichée : **24 h** ; rétention en base : **7 jours** (purge worker).
- API : `GET /api/v1/metro/graph` (plan statique), `GET /api/v1/metro/activity` (KPI + stats + graphe).
- Passages bruts : `GET /api/v1/task-runs` (filtres `station`, `line`, `hours`).

## Seuils affichés

- **Station lente** : médiane 24 h > 30 s et ≥ 3× la médiane 7 j de la même station.
- **Station figée** (blocage) : pas de battement depuis > 2 min.
- **Liens allumés** : au plus deux à la fois — dernier passage enregistré avec `edge_id`, et blocage en cours.

## Collecte inactive ou vide

Sans collecte active ou sans ligne dans `task_run` sur 24 h, l'interface propose un message explicite et un lien vers l'interrupteur des **Réglages › Application**.

## Téléphone

Pas de carte complète : mini-plan des 12 lignes, accordéons par ligne, page dédiée pour le détail d'une correspondance (`/sante/correspondance/:edgeId`).