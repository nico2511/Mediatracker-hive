<script setup lang="ts">
import { computed, ref } from "vue";

import { generateStrongPassword } from "../security/passwordGenerator";

const props = withDefaults(
  defineProps<{
    modelValue: string;
    label: string;
    autocomplete?: string;
    inputId?: string;
    minlength?: number;
    required?: boolean;
  }>(),
  {
    autocomplete: "new-password",
    inputId: "",
    minlength: 12,
    required: true,
  },
);

const emit = defineEmits<{
  "update:modelValue": [value: string];
}>();

const visible = ref(false);
const copyNotice = ref("");

const inputType = computed(() => (visible.value ? "text" : "password"));

function onInput(event: Event) {
  const el = event.target as HTMLInputElement;
  emit("update:modelValue", el.value);
}

function generate() {
  emit("update:modelValue", generateStrongPassword());
  visible.value = true;
  copyNotice.value = "";
}

async function copyToClipboard() {
  if (!props.modelValue) {
    return;
  }
  try {
    await navigator.clipboard.writeText(props.modelValue);
    copyNotice.value = "Copié dans le presse-papiers.";
  } catch {
    copyNotice.value = "Copie impossible sur cet appareil.";
  }
}
</script>

<template>
  <label class="password-with-tools">
    <span>{{ label }}</span>
    <input
      :id="inputId || undefined"
      :value="modelValue"
      :type="inputType"
      :autocomplete="autocomplete"
      :minlength="minlength"
      :required="required"
      @input="onInput"
    />
    <div class="password-with-tools__actions">
      <button type="button" class="btn btn-sm" @click="generate">Générer</button>
      <button type="button" class="btn btn-sm" @click="visible = !visible">
        {{ visible ? "Masquer" : "Afficher" }}
      </button>
      <button type="button" class="btn btn-sm" :disabled="!modelValue" @click="copyToClipboard">
        Copier
      </button>
    </div>
    <p v-if="copyNotice" class="hint password-with-tools__copy">{{ copyNotice }}</p>
  </label>
</template>

<style scoped>
.password-with-tools__actions {
  display: flex;
  flex-wrap: wrap;
  gap: 0.4rem;
  margin-top: 0.35rem;
}

.password-with-tools__actions .compact {
  flex: 1 1 auto;
  min-width: 5.5rem;
  max-width: 100%;
}

.password-with-tools__copy {
  margin-top: 0.25rem;
}
</style>