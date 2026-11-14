<script setup lang="ts">
import { nextTick, onUnmounted, ref, watch } from "vue";

import StatusIcon from "./StatusIcon.vue";

const props = withDefaults(
  defineProps<{
    label?: string;
    menuAriaLabel?: string;
    compactOnPhone?: boolean;
    triggerVariant?: "toggle" | "line" | "fiche";
    showStatusIcon?: boolean;
  }>(),
  {
    label: "Plus d'actions",
    menuAriaLabel: "Plus d'actions",
    compactOnPhone: false,
    triggerVariant: "line",
    showStatusIcon: true,
  },
);

const open = ref(false);
const triggerRef = ref<HTMLButtonElement | null>(null);
const menuRef = ref<HTMLElement | null>(null);
const menuStyle = ref({ top: "0px", left: "0px", minWidth: "250px" });
const focusIndex = ref(-1);

function menuItems(): HTMLElement[] {
  if (!menuRef.value) {
    return [];
  }
  return Array.from(
    menuRef.value.querySelectorAll<HTMLElement>('[role="menuitem"]:not([disabled])'),
  ).filter((node) => !node.closest("[hidden]"));
}

const VIEWPORT_MARGIN = 8;

function updatePosition() {
  const el = triggerRef.value;
  if (!el) {
    return;
  }
  const rect = el.getBoundingClientRect();
  const viewport = document.documentElement.clientWidth || window.innerWidth;
  const minWidth = Math.min(
    props.triggerVariant === "toggle" ? Math.max(rect.width, 250) : Math.max(rect.width, 12 * 16),
    viewport - VIEWPORT_MARGIN * 2,
  );
  const menuWidth = Math.max(menuRef.value?.offsetWidth ?? 0, minWidth);
  // Right-align on the trigger when it sits in the right half or when a left-aligned menu would overflow.
  const alignRight = rect.left + rect.width / 2 > viewport / 2 || rect.left + menuWidth > viewport - VIEWPORT_MARGIN;
  let left = alignRight ? rect.right - menuWidth : rect.left;
  left = Math.min(left, viewport - VIEWPORT_MARGIN - menuWidth);
  left = Math.max(left, VIEWPORT_MARGIN);
  menuStyle.value = {
    top: `${rect.bottom + 6}px`,
    left: `${left}px`,
    minWidth: `${minWidth}px`,
  };
}

function closeMenu() {
  if (!open.value) {
    return;
  }
  open.value = false;
  focusIndex.value = -1;
  triggerRef.value?.focus();
}

function openMenu() {
  open.value = true;
  focusIndex.value = -1;
  nextTick(() => {
    updatePosition();
    const items = menuItems();
    if (items.length) {
      focusIndex.value = 0;
      items[0].focus();
    }
  });
}

function toggleMenu() {
  if (open.value) {
    closeMenu();
  } else {
    openMenu();
  }
}

function onDocumentPointer(event: PointerEvent) {
  if (!open.value) {
    return;
  }
  const target = event.target as Node;
  if (menuRef.value?.contains(target) || triggerRef.value?.contains(target)) {
    return;
  }
  closeMenu();
}

function onDocumentKey(event: KeyboardEvent) {
  if (!open.value) {
    return;
  }
  if (event.key === "Escape") {
    event.preventDefault();
    closeMenu();
    return;
  }
  const items = menuItems();
  if (!items.length) {
    return;
  }
  if (event.key === "ArrowDown") {
    event.preventDefault();
    focusIndex.value = (focusIndex.value + 1) % items.length;
    items[focusIndex.value]?.focus();
  } else if (event.key === "ArrowUp") {
    event.preventDefault();
    focusIndex.value = focusIndex.value <= 0 ? items.length - 1 : focusIndex.value - 1;
    items[focusIndex.value]?.focus();
  } else if (event.key === "Home") {
    event.preventDefault();
    focusIndex.value = 0;
    items[0]?.focus();
  } else if (event.key === "End") {
    event.preventDefault();
    focusIndex.value = items.length - 1;
    items[items.length - 1]?.focus();
  }
}

function onMenuClick(event: MouseEvent) {
  const target = (event.target as HTMLElement).closest<HTMLElement>('[role="menuitem"]');
  if (!target || target.hasAttribute("disabled")) {
    return;
  }
  queueMicrotask(() => closeMenu());
}

function onTriggerKey(event: KeyboardEvent) {
  if (event.key === "ArrowDown" || event.key === "Enter" || event.key === " ") {
    event.preventDefault();
    if (!open.value) {
      openMenu();
    }
  }
}

watch(open, (isOpen) => {
  if (isOpen) {
    document.addEventListener("pointerdown", onDocumentPointer, true);
    document.addEventListener("keydown", onDocumentKey, true);
    window.addEventListener("resize", updatePosition);
    window.addEventListener("scroll", updatePosition, true);
  } else {
    document.removeEventListener("pointerdown", onDocumentPointer, true);
    document.removeEventListener("keydown", onDocumentKey, true);
    window.removeEventListener("resize", updatePosition);
    window.removeEventListener("scroll", updatePosition, true);
  }
});

onUnmounted(() => {
  document.removeEventListener("pointerdown", onDocumentPointer, true);
  document.removeEventListener("keydown", onDocumentKey, true);
  window.removeEventListener("resize", updatePosition);
  window.removeEventListener("scroll", updatePosition, true);
});
</script>

<template>
  <span class="action-menu-wrap">
    <button
      ref="triggerRef"
      type="button"
      class="action-menu-trigger"
      :class="{
        'action-menu-trigger-toggle': triggerVariant === 'toggle',
        'action-menu-trigger-line': triggerVariant === 'line',
        'action-menu-trigger-fiche': triggerVariant === 'fiche',
        'is-open': open,
        'is-compact-phone': compactOnPhone,
      }"
      aria-haspopup="menu"
      :aria-expanded="open ? 'true' : 'false'"
      :aria-label="compactOnPhone ? label : undefined"
      @click="toggleMenu"
      @keydown="onTriggerKey"
    >
      <StatusIcon v-if="showStatusIcon && triggerVariant !== 'toggle'" name="waiting" :label="label" />
      <span v-if="triggerVariant === 'toggle'" class="action-menu-trigger-text action-menu-trigger-text-full">{{ label }}</span>
      <span v-else class="action-menu-trigger-text">{{ label }}</span>
      <span v-if="triggerVariant === 'toggle'" class="action-menu-chevron" aria-hidden="true"></span>
      <span v-if="compactOnPhone" class="action-menu-trigger-ellipsis" aria-hidden="true">⋯</span>
    </button>
    <Teleport to="body">
      <div
        v-show="open"
        ref="menuRef"
        class="action-menu-popover"
        role="menu"
        :aria-label="menuAriaLabel"
        :style="menuStyle"
        @click="onMenuClick"
      >
        <slot />
      </div>
    </Teleport>
  </span>
</template>
