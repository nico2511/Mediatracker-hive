import { createApp } from "vue";

import App from "./App.vue";
import router from "./router";
import "./styles.css";
import { bindInstallPrompt } from "./pwa/pwaInstall";
import { initConnectivity, tryFlushQueue } from "./pwa/connectivity";
import { registerServiceWorker } from "./pwa/registerServiceWorker";

bindInstallPrompt();
registerServiceWorker();
initConnectivity();

if ("serviceWorker" in navigator) {
  navigator.serviceWorker.addEventListener("message", (event) => {
    if (event.data?.type === "MT_FLUSH_QUEUE") {
      void tryFlushQueue();
    }
  });
}

createApp(App).use(router).mount("#app");
