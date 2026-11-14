<script setup lang="ts">
import { nextTick, ref } from "vue";
import type { RouteLocationRaw } from "vue-router";
import { useRouter } from "vue-router";

import { useLongPress, type PressPoint } from "../composables/useLongPress";
import StatusIcon from "./StatusIcon.vue";
import TmdbPosterImg from "./TmdbPosterImg.vue";

const props = withDefaults(
  defineProps<{
    posterUrl?: string | null;
    title: string;
    kindLabel: string;
    ficheTo?: RouteLocationRaw | null;
    fallback?: string;
    offlineGhost?: boolean;
    lazy?: boolean;
    hideMoreButton?: boolean;
  }>(),
  {
    posterUrl: null,
    ficheTo: null,
    fallback: "",
    offlineGhost: false,
    lazy: true,
    hideMoreButton: false,
  },
);

const emit = defineEmits<{
  open: [];
}>();

const router = useRouter();

const displayFallback = () => props.fallback || props.title || props.kindLabel;

function onPosterClick() {
  emit("open");
}

/* ---- Menu de carte : appui long (tactile), clic droit, touche Menu, bouton « Actions » au clavier ---- */

interface MenuItem {
  key: string;
  label: string;
  icon: string;
  iconHtml?: string;
  disabled: boolean;
  hint?: string;
  run: () => void;
}

const actionsHost = ref<HTMLElement | null>(null);
const posterHit = ref<HTMLButtonElement | null>(null);
const menuEl = ref<HTMLElement | null>(null);
const menuOpen = ref(false);
const menuItems = ref<MenuItem[]>([]);
const menuStyle = ref<Record<string, string>>({});
const menuId = `pc-menu-${Math.random().toString(36).slice(2, 9)}`;
const sheet = () => window.matchMedia("(hover: none), (max-width: 720px)").matches;
/** Ignore le contextmenu Android juste après ouverture tactile (≈500 ms). */
const MENU_CONTEXTMENU_GUARD_MS = 800;
const menuOpenedAt = ref(0);
const menuOpenedViaTouch = ref(false);

/** Les actions du slot #actions (déjà présentes dans chaque vue) deviennent des entrées du menu. */
function collectSlotActions(): MenuItem[] {
  const host = actionsHost.value;
  if (!host) return [];
  return Array.from(host.querySelectorAll<HTMLElement>("button, a[href]")).map((el, index) => {
    const svg = el.querySelector("svg");
    const label = el.getAttribute("aria-label") || el.textContent?.trim() || el.title || "Action";
    const disabled = el instanceof HTMLButtonElement && el.disabled;
    return {
      key: `slot-${index}`,
      label,
      icon: "",
      iconHtml: svg ? svg.outerHTML.replace('role="img"', 'aria-hidden="true"') : "",
      disabled,
      hint: disabled ? el.title || undefined : undefined,
      run: () => el.click(),
    };
  });
}

function buildItems(): MenuItem[] {
  const fiche: MenuItem = {
    key: "fiche",
    label: "Fiche",
    icon: "file",
    disabled: false,
    run: () => (props.ficheTo ? void router.push(props.ficheTo) : emit("open")),
  };
  return [fiche, ...collectSlotActions()];
}

async function openMenu(at: PressPoint) {
  menuItems.value = buildItems();
  if (sheet()) {
    menuStyle.value = {};
  } else {
    const rect = posterHit.value?.getBoundingClientRect();
    const x = at.via === "keyboard" && rect ? rect.left : at.x;
    const y = at.via === "keyboard" && rect ? rect.bottom : at.y;
    menuStyle.value = {
      left: `${Math.min(x, window.innerWidth - 224)}px`,
      top: `${Math.min(y, window.innerHeight - 280)}px`,
    };
  }
  menuOpen.value = true;
  menuOpenedAt.value = Date.now();
  menuOpenedViaTouch.value = at.via === "touch" || at.via === "pen";
  await nextTick();
  menuEl.value?.querySelector<HTMLElement>('[role="menuitem"]:not([aria-disabled="true"])')?.focus();
}

function closeMenu(restoreFocus = true) {
  menuOpen.value = false;
  menuOpenedViaTouch.value = false;
  if (restoreFocus) posterHit.value?.focus();
}

function onBackdropContextmenu(event: MouseEvent) {
  event.preventDefault();
  if (
    menuOpenedViaTouch.value &&
    menuOpenedAt.value > 0 &&
    Date.now() - menuOpenedAt.value < MENU_CONTEXTMENU_GUARD_MS
  ) {
    return;
  }
  closeMenu();
}

function choose(item: MenuItem) {
  if (item.disabled) return;
  closeMenu(false);
  item.run();
}

function onMenuKeydown(event: KeyboardEvent) {
  const items = Array.from(menuEl.value?.querySelectorAll<HTMLElement>('[role="menuitem"]') ?? []);
  const current = items.indexOf(document.activeElement as HTMLElement);
  const move = (index: number) => items[(index + items.length) % items.length]?.focus();
  if (event.key === "ArrowDown") move(current + 1);
  else if (event.key === "ArrowUp") move(current - 1);
  else if (event.key === "Home") move(0);
  else if (event.key === "End") move(items.length - 1);
  else if (event.key === "Escape") closeMenu();
  else if (event.key === "Tab") return closeMenu(false);
  else return;
  event.preventDefault();
}

const press = useLongPress(openMenu);
</script>

<template>
  <article
    class="poster-card pc-card"
    :class="{ 'is-menu-open': menuOpen }"
    @contextmenu="press.onContextmenu"
  >
    <div
      class="pc-shell"
      @pointerdown="press.onPointerdown"
      @pointermove="press.onPointermove"
      @pointerup="press.onPointerup"
      @pointercancel="press.onPointercancel"
      @touchend="press.onTouchend"
      @click.capture="press.onClickCapture"
    >
      <slot name="shell-extra" />
      <button
        ref="posterHit"
        type="button"
        class="poster-hit"
        :aria-describedby="`${menuId}-hint`"
        @click="onPosterClick"
      >
        <span class="poster-frame">
          <span class="pc-type">{{ kindLabel }}</span>
          <TmdbPosterImg
            :url="posterUrl"
            :alt="title"
            :fallback="displayFallback()"
            :offline-ghost="offlineGhost"
            :lazy="lazy"
            draggable="false"
          />
          <slot name="overlay" />
        </span>
      </button>
      <span :id="`${menuId}-hint`" class="sr-only">Appui long ou touche Menu : plus d'actions</span>
      <router-link v-if="ficheTo" class="pc-fiche" :to="ficheTo">Fiche</router-link>
      <button v-else type="button" class="pc-fiche" @click.stop="emit('open')">Fiche</button>
      <button
        v-if="!hideMoreButton"
        type="button"
        class="pc-more"
        aria-haspopup="menu"
        :aria-expanded="menuOpen"
        :aria-controls="menuId"
        :aria-label="`Actions pour ${title}`"
        @click.stop="openMenu({ x: 0, y: 0, via: 'keyboard' })"
      >
        ⋯
      </button>
    </div>
    <div class="pc-meta">
      <slot name="meta" />
    </div>
    <div ref="actionsHost" class="pc-actions">
      <slot name="actions" />
    </div>

    <Teleport to="body">
      <template v-if="menuOpen">
        <div class="pc-menu-backdrop" @click="closeMenu()" @contextmenu="onBackdropContextmenu" />
        <ul
          :id="menuId"
          ref="menuEl"
          class="pc-menu"
          role="menu"
          :aria-label="`Actions pour ${title}`"
          :style="menuStyle"
          @keydown="onMenuKeydown"
        >
          <li role="none" class="pc-menu-head" aria-hidden="true">
            <strong>{{ title }}</strong>
            <span class="hint">{{ kindLabel }}</span>
          </li>
          <li v-for="item in menuItems" :key="item.key" role="none">
            <button
              type="button"
              role="menuitem"
              class="pc-menu-item"
              tabindex="-1"
              :aria-disabled="item.disabled ? 'true' : undefined"
              :title="item.hint"
              @click="choose(item)"
            >
              <StatusIcon v-if="item.icon" :name="item.icon" label="" aria-hidden="true" />
              <span v-else-if="item.iconHtml" class="pc-menu-icon" aria-hidden="true" v-html="item.iconHtml" />
              {{ item.label }}
            </button>
          </li>
          <li role="none">
            <button type="button" class="pc-menu-item pc-menu-cancel" tabindex="-1" @click="closeMenu()">Annuler</button>
          </li>
        </ul>
      </template>
    </Teleport>
  </article>
</template>
