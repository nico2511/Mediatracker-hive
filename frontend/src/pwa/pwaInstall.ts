import { ref, shallowRef } from "vue";

const INSTALL_LATER_KEY = "mt.pwa.install.later";

export interface BeforeInstallPromptEvent extends Event {
  prompt: () => Promise<void>;
  userChoice: Promise<{ outcome: "accepted" | "dismissed" }>;
}

const deferred = shallowRef<BeforeInstallPromptEvent | null>(null);
const installOpen = ref(false);

function laterUntil(): number {
  try {
    const raw = localStorage.getItem(INSTALL_LATER_KEY);
    return raw ? Number(raw) : 0;
  } catch {
    return 0;
  }
}

export function markInstallLater(days = 30): void {
  const until = Date.now() + days * 24 * 60 * 60 * 1000;
  try {
    localStorage.setItem(INSTALL_LATER_KEY, String(until));
  } catch {
    /* ignore */
  }
  installOpen.value = false;
}

export function canShowInstallPrompt(): boolean {
  if (!deferred.value) {
    return false;
  }
  if (!navigator.onLine) {
    return false;
  }
  return Date.now() >= laterUntil();
}

export function openInstallPanel(): void {
  if (deferred.value) {
    installOpen.value = true;
  }
}

export function closeInstallPanel(): void {
  installOpen.value = false;
}

export async function runInstallPrompt(): Promise<"accepted" | "dismissed" | "unavailable"> {
  const event = deferred.value;
  if (!event) {
    return "unavailable";
  }
  await event.prompt();
  const choice = await event.userChoice;
  if (choice.outcome === "accepted") {
    deferred.value = null;
    installOpen.value = false;
  }
  return choice.outcome;
}

export function bindInstallPrompt(): void {
  window.addEventListener("beforeinstallprompt", (event) => {
    event.preventDefault();
    deferred.value = event as BeforeInstallPromptEvent;
  });
  window.addEventListener("appinstalled", () => {
    deferred.value = null;
    installOpen.value = false;
  });
}

export function usePwaInstall() {
  return {
    deferred,
    installOpen,
    canShowInstallPrompt,
    openInstallPanel,
    closeInstallPanel,
    runInstallPrompt,
    markInstallLater,
  };
}
