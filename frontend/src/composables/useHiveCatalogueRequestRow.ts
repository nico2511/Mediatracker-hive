import { ref } from "vue";

import { api, userFacingApiError } from "../api";

export type HiveRequestRowState = "idle" | "sending" | "requested" | "error";

/** États Envoi… / Demandé / erreur par ligne pour POST catalogue/request. */
export function useHiveCatalogueRequestRow() {
  const states = ref<Record<string, HiveRequestRowState>>({});
  const errors = ref<Record<string, string>>({});

  function stateFor(key: string): HiveRequestRowState {
    return states.value[key] ?? "idle";
  }

  function errorFor(key: string): string {
    return errors.value[key] ?? "";
  }

  async function postRequest(
    key: string,
    workKey: string,
    body: Record<string, unknown>,
  ): Promise<boolean> {
    states.value = { ...states.value, [key]: "sending" };
    errors.value = { ...errors.value, [key]: "" };
    try {
      await api(`/api/v1/hive/catalogue/${workKey}/request`, {
        method: "POST",
        body: JSON.stringify(body),
      });
      states.value = { ...states.value, [key]: "requested" };
      return true;
    } catch (err) {
      states.value = { ...states.value, [key]: "error" };
      errors.value = {
        ...errors.value,
        [key]: userFacingApiError(err, "Transfert indisponible."),
      };
      return false;
    }
  }

  return { stateFor, errorFor, postRequest };
}