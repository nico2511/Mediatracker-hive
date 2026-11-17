import { ref } from "vue";

import { hiveVersionProfileCompatible, type HiveVersionProfileRow } from "../hive/hiveVersionProfile";

export function useHiveOutOfProfileConfirm() {
  const pendingId = ref<string | null>(null);

  function resetConfirm() {
    pendingId.value = null;
  }

  function beginOrConfirm(
    targetId: string,
    version: HiveVersionProfileRow | null | undefined,
    onConfirm: () => void,
  ): void {
    if (!version) {
      return;
    }
    if (hiveVersionProfileCompatible(version)) {
      pendingId.value = null;
      onConfirm();
      return;
    }
    if (pendingId.value !== targetId) {
      pendingId.value = targetId;
      return;
    }
    pendingId.value = null;
    onConfirm();
  }

  function needsConfirm(targetId: string, version: HiveVersionProfileRow | null | undefined): boolean {
    return (
      pendingId.value === targetId &&
      Boolean(version) &&
      !hiveVersionProfileCompatible(version)
    );
  }

  return { pendingId, resetConfirm, beginOrConfirm, needsConfirm };
}