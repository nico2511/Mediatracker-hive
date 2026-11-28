# Application

URL : `/reglages/application`

Propose l’**installation PWA** (Progressive Web App) lorsque le navigateur expose l’événement `beforeinstallprompt`. Hors ligne partiel : file d’ids TMDB selon l’implémentation PWA du frontend.

## Collecte des passages (diagnostic réseau)

Interrupteur réservé aux administrateurs : active l’enregistrement des passages worker dans la table interne `task_run`, pour préparer la future carte réseau (Santé). Durées : 1 h, 6 h, 24 h ou sans limite. Hors période, **aucune** écriture (y compris les battements d’un blocage en cours). La purge automatique conserve 7 jours d’historique.
