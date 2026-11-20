<script setup lang="ts">
import { ref } from "vue";
import { useRouter } from "vue-router";

import {api, userFacingApiError} from "../api";
import { prefetchAppSettingsCaches } from "../appSettingsCaches";
import { clearSession } from "../session";

const router = useRouter();
const username = ref("admin");
const password = ref("");
const error = ref("");

const OFFLINE_LOGIN_MESSAGE = "Hors ligne : reconnectez-vous quand le réseau revient";

async function submit() {
  error.value = "";
  if (!navigator.onLine) {
    error.value = OFFLINE_LOGIN_MESSAGE;
    return;
  }
  try {
    await api("/api/v1/auth/login", {
      method: "POST",
      body: JSON.stringify({ username: username.value, password: password.value }),
    });
    clearSession();
    void prefetchAppSettingsCaches();
    await router.push("/");
  } catch (err) {
    if (!navigator.onLine) {
      error.value = OFFLINE_LOGIN_MESSAGE;
      return;
    }
    error.value = userFacingApiError(err, "Connexion impossible.");
  }
}
</script>

<template>
  <main class="login">
    <h1>MediaTracker</h1>
    <form class="panel" @submit.prevent="submit">
      <label>
        <span>Nom d'utilisateur</span>
        <input v-model="username" autocomplete="username" required />
      </label>
      <label>
        <span>Mot de passe</span>
        <input v-model="password" type="password" autocomplete="current-password" required />
      </label>
      <p v-if="error" class="error">{{ error }}</p>
      <button type="submit">Se connecter</button>
    </form>
  </main>
</template>
