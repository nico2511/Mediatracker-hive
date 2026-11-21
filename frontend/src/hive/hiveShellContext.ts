import { type InjectionKey, inject } from "vue";

export type HiveShellContext = {
  refreshTabBadges: () => void | Promise<void>;
};

export const hiveShellContextKey: InjectionKey<HiveShellContext> = Symbol("hiveShellContext");

export function useHiveShellContext(): HiveShellContext | null {
  return inject(hiveShellContextKey, null);
}

export function notifyHiveTabBadges(ctx: HiveShellContext | null): void {
  void ctx?.refreshTabBadges();
}