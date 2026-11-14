<script setup lang="ts">
import { onMounted, ref, watch } from "vue";
import { useRouter } from "vue-router";

import {api, userFacingApiError} from "../api";
import CollapsibleSection from "../components/CollapsibleSection.vue";
import PasswordFieldWithGenerator from "../components/PasswordFieldWithGenerator.vue";
import { fetchSettings, patchSettings } from "../settings";
import { clearSession, noteSetupCompleted } from "../session";

/** Exemple de ligne de journal (sans jeton réel). */
const SETUP_BOOTSTRAP_LOG_EXAMPLE =
  "Aucun compte administrateur : pour créer le premier compte via l'assistant, " +
  "saisissez le jeton d'installation suivant (ou définissez SETUP_TOKEN pour le fixer) : xxxx-xxxx";

const router = useRouter();
const step = ref(0);
const needsPassword = ref(true);
const error = ref("");
const notice = ref("");

const username = ref("admin");
const password = ref("");
const setupToken = ref("");
const mediaPath = ref("/data/media");
const moviesPath = ref("/data/media/Films");
const seriesPath = ref("/data/media/Series");
const downloadsPath = ref("/data/downloads");
const trashPath = ref("/data/.trash");
const volumeWarning = ref("");
const alldebridKey = ref("");
const tmdbToken = ref("");
const jellyfinUrl = ref("http://jellyfin:8096");
const jellyfinKey = ref("");
const jellyfinUser = ref("");
const jellyfinMap = ref("");
const suggestedMap = ref("");
const pathWarnings = ref<string[]>([]);
const wawacityUrl = ref("");
const freeTelechargerUrl = ref("");
const cometUrls = ref("");
const torrentioUrl = ref("");
const sourcesFieldError = ref("");
const haHost = ref("http://homeassistant.local:8123");
const haId = ref("mediatracker");
const scanSummary = ref("");

const titles = [
  "Mot de passe",
  "Dossiers",
  "AllDebrid",
  "TMDB",
  "Jellyfin",
  "Sources",
  "Home Assistant",
  "Terminer",
];

onMounted(async () => {
  const status = await api<{ needs_password: boolean; completed: boolean }>("/api/v1/setup/status");
  needsPassword.value = status.needs_password;
  if (!status.needs_password) {
    step.value = 1;
    try {
      await api("/api/v1/auth/me");
      await loadStorage();
    } catch {
      await router.push("/login");
    }
  }
  if (status.completed) {
    await router.push("/");
  }
});

watch(step, (value) => {
  if (value === 5 && !needsPassword.value) {
    void loadSourceFields();
  }
});

function rejectNonHttpUrl(raw: string): boolean {
  const text = raw.trim();
  return Boolean(text) && !/^https?:\/\//i.test(text);
}

async function loadSourceFields() {
  try {
    const page = await fetchSettings();
    for (const row of page.settings) {
      if (row.key === "wawacity_base_url" && row.value) {
        wawacityUrl.value = String(row.value);
      }
      if (row.key === "torrentio_base_url" && row.value) {
        torrentioUrl.value = String(row.value);
      }
      if (row.key === "comet_base_urls" && row.value) {
        cometUrls.value = String(row.value)
          .split("\n")
          .map((line) => line.trim())
          .filter(Boolean)
          .join("\n");
      }
    }
    const providers = await api<{ items?: { type: string; config: Record<string, unknown> }[] }>(
      "/api/v1/providers?kind=source",
    );
    for (const item of providers.items ?? []) {
      const url = item.config?.base_url;
      if (item.type === "free_telecharger" && typeof url === "string" && url.trim()) {
        freeTelechargerUrl.value = url.trim();
      }
    }
  } catch {
    /* valeurs par défaut / vides */
  }
}

async function loadStorage() {
  const layout = await api<{
    media_dir: string;
    movies_dir: string;
    series_dir: string;
    downloads_dir: string;
    trash_dir: string;
    warning: string | null;
  }>("/api/v1/storage");
  mediaPath.value = layout.media_dir;
  moviesPath.value = layout.movies_dir;
  seriesPath.value = layout.series_dir;
  downloadsPath.value = layout.downloads_dir;
  trashPath.value = layout.trash_dir;
  volumeWarning.value = layout.warning || "";
  const page = await fetchSettings();
  for (const row of page.settings) {
    if (row.key === "jellyfin_url" && row.value) {
      jellyfinUrl.value = row.value;
    }
    if (row.key === "jellyfin_path_map" && row.value) {
      jellyfinMap.value = row.value;
    }
  }
}

async function createAccount() {
  error.value = "";
  await api("/api/v1/setup/account", {
    method: "POST",
    body: JSON.stringify({
      username: username.value,
      password: password.value,
      setup_token: setupToken.value,
    }),
  });
  needsPassword.value = false;
  clearSession();
  await loadStorage();
  step.value = 1;
}

async function saveRoots() {
  error.value = "";
  await patchSettings({
    values: {
      media_dir: mediaPath.value,
      library_movies_path: moviesPath.value,
      library_series_path: seriesPath.value,
      downloads_dir: downloadsPath.value,
      trash_dir: trashPath.value,
    },
  });
  step.value = 2;
}

async function saveAllDebrid(test: boolean) {
  error.value = "";
  notice.value = "";
  if (alldebridKey.value) {
    const page = await api<{ items: { id: number; type: string }[] }>("/api/v1/providers");
    const row = page.items.find((item) => item.type === "alldebrid");
    if (row) {
      await api(`/api/v1/providers/${row.id}`, {
        method: "PATCH",
        body: JSON.stringify({ enabled: true, config: { api_key: alldebridKey.value } }),
      });
    } else {
      await api("/api/v1/providers", {
        method: "POST",
        body: JSON.stringify({
          kind: "debrid",
          type: "alldebrid",
          name: "AllDebrid",
          enabled: true,
          config: { api_key: alldebridKey.value },
        }),
      });
    }
  }
  if (test) {
    const result = await api<{ ok: boolean; message: string }>("/api/v1/setup/probe", {
      method: "POST",
      body: JSON.stringify({ target: "alldebrid", value: alldebridKey.value }),
    });
    notice.value = result.message;
    if (!result.ok) {
      error.value = result.message;
      return;
    }
  }
  alldebridKey.value = "";
  step.value = 3;
}

async function saveTmdb(test: boolean) {
  error.value = "";
  notice.value = "";
  if (tmdbToken.value) {
    await patchSettings({ values: { tmdb_read_token: tmdbToken.value } });
  }
  if (test) {
    const result = await api<{ ok: boolean; message: string }>("/api/v1/setup/probe", {
      method: "POST",
      body: JSON.stringify({ target: "tmdb", value: tmdbToken.value }),
    });
    notice.value = result.message;
    if (!result.ok) {
      error.value = result.message;
      return;
    }
  }
  tmdbToken.value = "";
  step.value = 4;
}

async function testJellyfin() {
  error.value = "";
  notice.value = "";
  pathWarnings.value = [];
  const result = await api<{
    ok: boolean;
    message: string;
    user_id: string | null;
    path_warnings: string[];
    suggested_path_map: string;
  }>("/api/v1/integrations/jellyfin/test", {
    method: "POST",
    body: JSON.stringify({
      url: jellyfinUrl.value,
      api_key: jellyfinKey.value || null,
      user_id: jellyfinUser.value,
      path_map: jellyfinMap.value,
    }),
  });
  notice.value = result.message;
  pathWarnings.value = result.path_warnings || [];
  suggestedMap.value = result.suggested_path_map || "";
  if (suggestedMap.value && !jellyfinMap.value.trim()) {
    jellyfinMap.value = suggestedMap.value;
  }
  if (result.ok && result.user_id && !jellyfinUser.value) {
    jellyfinUser.value = result.user_id;
  }
  if (!result.ok) {
    error.value = result.message;
  }
}

async function saveJellyfin() {
  error.value = "";
  const values: Record<string, string> = {
    jellyfin_url: jellyfinUrl.value,
    jellyfin_user_id: jellyfinUser.value,
    jellyfin_path_map: jellyfinMap.value,
  };
  if (jellyfinKey.value) {
    values.jellyfin_api_key = jellyfinKey.value;
  }
  await patchSettings({ values });
  jellyfinKey.value = "";
  step.value = 5;
}

async function saveSources(testWawacity: boolean) {
  error.value = "";
  notice.value = "";
  sourcesFieldError.value = "";
  const candidates = [
    wawacityUrl.value,
    freeTelechargerUrl.value,
    ...cometUrls.value.split("\n"),
    torrentioUrl.value,
  ];
  for (const raw of candidates) {
    if (rejectNonHttpUrl(raw)) {
      sourcesFieldError.value = "Chaque adresse renseignée doit commencer par http:// ou https://.";
      error.value = sourcesFieldError.value;
      return;
    }
  }
  const body: Record<string, string> = {};
  if (wawacityUrl.value.trim()) {
    body.wawacity_base_url = wawacityUrl.value.trim();
  }
  if (torrentioUrl.value.trim()) {
    body.torrentio_base_url = torrentioUrl.value.trim();
  }
  if (cometUrls.value.trim()) {
    body.comet_base_urls = cometUrls.value.trim();
  }
  if (freeTelechargerUrl.value.trim()) {
    body.free_telecharger_base_url = freeTelechargerUrl.value.trim();
  }
  if (Object.keys(body).length) {
    await api<{ saved: string[] }>("/api/v1/setup/sources", {
      method: "POST",
      body: JSON.stringify(body),
    });
  }
  if (testWawacity && wawacityUrl.value.trim()) {
    const result = await api<{ ok: boolean; message: string }>("/api/v1/setup/probe", {
      method: "POST",
      body: JSON.stringify({ target: "wawacity", value: wawacityUrl.value.trim() }),
    });
    notice.value = result.message;
    if (!result.ok) {
      error.value = result.message;
      return;
    }
  }
  step.value = 6;
}

async function saveHomeAssistant(test: boolean) {
  error.value = "";
  notice.value = "";
  const url = `${haHost.value.replace(/\/$/, "")}/api/webhook/${haId.value || "mediatracker"}`;
  if (test) {
    const result = await api<{ ok: boolean; message: string }>("/api/v1/setup/probe", {
      method: "POST",
      body: JSON.stringify({ target: "homeassistant", value: url }),
    });
    notice.value = result.message;
    if (!result.ok) {
      error.value = result.message;
      return;
    }
  }
  const catalog = await api<{ events: { type: string }[] }>("/api/v1/events/notifications");
  await api("/api/v1/webhooks", {
    method: "POST",
    body: JSON.stringify({
      name: "Home Assistant",
      type: "homeassistant",
      url,
      events: catalog.events.map((row) => row.type),
      enabled: true,
    }),
  });
  step.value = 7;
}

async function finish() {
  error.value = "";
  const report = await api<{ seen: number; matched: number; review: number }>("/api/v1/setup/complete", {
    method: "POST",
  });
  scanSummary.value = `${report.seen} fichiers vus, ${report.matched} associés, ${report.review} à relire.`;
  noteSetupCompleted();
}

async function run(action: () => Promise<void>) {
  error.value = "";
  try {
    await action();
  } catch (err) {
    error.value = userFacingApiError(err, "Étape impossible.");
  }
}
</script>

<template>
  <main class="login wizard">
    <h1>Première utilisation</h1>
    <p class="hint">Étape {{ step + 1 }} / {{ titles.length }} — {{ titles[step] }}</p>
    <p v-if="error" class="error">{{ error }}</p>
    <p v-if="notice && !error" class="hint">{{ notice }}</p>

    <form v-if="step === 0" class="panel stack" @submit.prevent="run(createAccount)">
      <p class="hint">
        Choisissez le mot de passe du compte unique : au moins 12 caractères, deux types de caractères
        (lettres, chiffres, symboles), sans suite évidente ni mot de passe trop courant, et sans le nom
        d'utilisateur.
      </p>
      <label>
        <span>Nom d'utilisateur</span>
        <input v-model="username" autocomplete="username" required />
      </label>
      <PasswordFieldWithGenerator v-model="password" label="Mot de passe" autocomplete="new-password" />
      <label>
        <span>Jeton d'installation</span>
        <input v-model="setupToken" type="password" autocomplete="off" required />
        <span class="hint" data-testid="setup-token-help">
          Obligatoire. Ce jeton autorise la création du premier compte administrateur.
        </span>
      </label>
      <CollapsibleSection title="Où le trouver ?" data-testid="setup-token-where">
        <p class="hint">
          Cherchez dans les journaux du conteneur MediaTracker (Dockge → onglet Logs, ou
          <code>docker logs &lt;conteneur&gt;</code>) la ligne&nbsp;:
        </p>
        <div class="journal-tech" data-setup-log-example>
          <pre>{{ SETUP_BOOTSTRAP_LOG_EXAMPLE }}</pre>
        </div>
        <p class="hint">
          Vous pouvez aussi fixer le jeton avec la variable d'environnement <code>SETUP_TOKEN</code>. Une fois le
          compte créé, le jeton n'est plus valable.
        </p>
      </CollapsibleSection>
      <button type="submit">Créer le compte</button>
    </form>

    <form v-else-if="step === 1" class="panel stack" @submit.prevent="run(saveRoots)">
      <p class="hint">
        Films et séries, sur le même volume que les téléchargements et la corbeille, pour que le rangement
        soit un renommage.
      </p>
      <p v-if="volumeWarning" class="warn">{{ volumeWarning }}</p>
      <label>
        <span>Dossier médias</span>
        <input v-model="mediaPath" required />
        <span class="hint">Base des films et des séries quand leurs dossiers ne sont pas renseignés.</span>
      </label>
      <label>
        <span>Films</span>
        <input v-model="moviesPath" required />
      </label>
      <label>
        <span>Séries</span>
        <input v-model="seriesPath" required />
        <span class="hint">Un accent dans le chemin, comme <code>/data/Séries</code>, est accepté.</span>
      </label>
      <label>
        <span>Téléchargements</span>
        <input v-model="downloadsPath" required />
      </label>
      <label>
        <span>Corbeille</span>
        <input v-model="trashPath" required />
        <span class="hint">Ces quatre dossiers doivent rester sur le même disque : le rangement est un renommage.</span>
      </label>
      <button type="submit">Continuer</button>
    </form>

    <form v-else-if="step === 2" class="panel stack" @submit.prevent="run(() => saveAllDebrid(false))">
      <p class="hint">Clé API du compte AllDebrid. Elle est chiffrée et n'est plus affichée ensuite.</p>
      <label>
        <span>Clé API</span>
        <input v-model="alldebridKey" type="password" autocomplete="off" />
      </label>
      <div class="row">
        <button type="button" @click="run(() => saveAllDebrid(true))">Tester</button>
        <button type="submit">Continuer</button>
      </div>
    </form>

    <form v-else-if="step === 3" class="panel stack" @submit.prevent="run(() => saveTmdb(false))">
      <p class="hint">
        Jeton de lecture v4 sur themoviedb.org (Paramètres du compte, API). Sans jeton, le titre et l'année
        suffisent pour suivre une fiche.
      </p>
      <label>
        <span>Jeton TMDB</span>
        <input v-model="tmdbToken" type="password" autocomplete="off" />
      </label>
      <div class="row">
        <button type="button" @click="run(() => saveTmdb(true))">Tester</button>
        <button type="submit">Continuer</button>
      </div>
    </form>

    <form v-else-if="step === 4" class="panel stack" @submit.prevent="run(saveJellyfin)">
      <p class="hint">
        L'URL et la correspondance reprennent l'environnement quand il les définit. Sinon, sur le même
        réseau Docker : <code>http://jellyfin:8096</code>. La clé se crée dans Jellyfin, tableau de bord
        (engrenage), Clés API, puis le bouton plus. S'il n'y a qu'un utilisateur, son identifiant est proposé
        au test.
      </p>
      <label>
        <span>URL</span>
        <input v-model="jellyfinUrl" required />
      </label>
      <label>
        <span>Clé API</span>
        <input v-model="jellyfinKey" type="password" autocomplete="off" />
      </label>
      <label>
        <span>Identifiant utilisateur</span>
        <input v-model="jellyfinUser" placeholder="Laissé vide s'il n'y a qu'un compte" />
      </label>
      <label>
        <span>Correspondance des chemins</span>
        <input v-model="jellyfinMap" placeholder="/data/movies=/data/Films;/data/tvshows=/data/Séries" />
        <span class="hint">
          Le test lit les bibliothèques Jellyfin et propose une correspondance. Les chemins Jellyfin
          ne sont pas modifiés.
        </span>
      </label>
      <p v-if="suggestedMap" class="hint">Proposition : <code>{{ suggestedMap }}</code></p>
      <p v-for="warning in pathWarnings" :key="warning" class="warn">{{ warning }}</p>
      <div class="row">
        <button type="button" @click="run(testJellyfin)">Tester</button>
        <button type="submit">Continuer</button>
      </div>
    </form>

    <form
      v-else-if="step === 5"
      class="panel stack setup-sources-form"
      data-testid="setup-sources-form"
      @submit.prevent="run(() => saveSources(false))"
    >
      <p class="hint">
        Adresses optionnelles des sources de liens. Laissez vide pour garder la valeur actuelle ou le défaut du
        conteneur. L'ordre et le détail se règlent ensuite dans Réglages → Sources.
      </p>
      <fieldset class="stack setup-sources-fieldset">
        <legend class="setup-sources-legend">Adresses des sources</legend>
        <label>
          <span>Wawacity</span>
          <input v-model="wawacityUrl" type="url" inputmode="url" placeholder="https://domaine-actuel.example" />
          <span class="hint">Sans URL, aucun scan Wawacity.</span>
        </label>
        <label>
          <span>Free-Télécharger</span>
          <input v-model="freeTelechargerUrl" type="url" inputmode="url" placeholder="https://catalogue-neutre.example" />
        </label>
        <label>
          <span>Comet (une URL par ligne)</span>
          <textarea v-model="cometUrls" rows="2" placeholder="https://comet.feels.legal"></textarea>
          <span class="hint">Instances publiques sans clé AllDebrid dans l'URL.</span>
        </label>
        <label>
          <span>Torrentio</span>
          <input
            v-model="torrentioUrl"
            type="url"
            inputmode="url"
            placeholder="https://torrentio.strem.fun"
          />
        </label>
      </fieldset>
      <p v-if="sourcesFieldError" class="error">{{ sourcesFieldError }}</p>
      <div class="row setup-sources-actions">
        <button type="button" @click="run(() => saveSources(true))">Tester Wawacity</button>
        <button type="submit">Continuer</button>
        <button type="button" @click="step = 6">Passer</button>
      </div>
    </form>

    <form v-else-if="step === 6" class="panel stack" @submit.prevent="run(() => saveHomeAssistant(false))">
      <p class="hint">
        Automatisation webhook dans Home Assistant, identifiant par défaut <code>mediatracker</code>.
        L'identifiant dans l'URL est le secret.
      </p>
      <label>
        <span>URL du serveur</span>
        <input v-model="haHost" />
      </label>
      <label>
        <span>Identifiant du webhook</span>
        <input v-model="haId" />
      </label>
      <div class="row">
        <button type="button" @click="run(() => saveHomeAssistant(true))">Tester et enregistrer</button>
        <button type="button" @click="step = 7">Passer</button>
      </div>
    </form>

    <section v-else class="panel stack">
      <p class="hint">L'analyse de la bibliothèque locale part maintenant.</p>
      <button type="button" @click="run(finish)">Lancer l'analyse</button>
      <p v-if="scanSummary">{{ scanSummary }}</p>
      <router-link v-if="scanSummary" to="/">Aller à l'accueil</router-link>
    </section>
  </main>
</template>
