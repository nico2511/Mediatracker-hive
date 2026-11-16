export function discoverBlockedOnDesktop(): boolean {
  return typeof window !== "undefined" && window.matchMedia("(min-width: 721px)").matches;
}