<script setup lang="ts">
import { markInstallLater, runInstallPrompt, usePwaInstall } from "../pwa/pwaInstall";

const { installOpen, closeInstallPanel } = usePwaInstall();

async function install() {
  await runInstallPrompt();
}

function later() {
  markInstallLater(30);
  closeInstallPanel();
}
</script>

<template>
  <div v-if="installOpen" class="pwa-install-scrim" @click.self="closeInstallPanel">
    <section class="pwa-install" role="dialog" aria-labelledby="pwa-install-title">
      <div class="pwa-install-grab" aria-hidden="true" />
      <div class="pwa-install-head">
        <img src="/icon-192.png" width="64" height="64" alt="" />
        <div>
          <h2 id="pwa-install-title">Installer MediaTracker</h2>
          <p class="hint">Application web de votre serveur</p>
        </div>
      </div>
      <ul class="pwa-install-list">
        <li>✓ S'ouvre comme une app, depuis l'écran d'accueil, sans barre du navigateur.</li>
        <li>✓ Fonctionne sans réseau : vous pouvez mettre des ID TMDB en file, envoyés au retour du serveur.</li>
        <li>✓ Ne garde que l'interface sur l'appareil, aucun média.</li>
      </ul>
      <div class="pwa-install-actions">
        <button type="button" class="primary" @click="install">Installer</button>
        <button type="button" class="link-button" @click="later">Plus tard</button>
      </div>
      <p class="hint pwa-install-later">
        « Plus tard » masque l'invite 30 jours. Toujours possible depuis Réglages › Application.
      </p>
    </section>
  </div>
</template>
