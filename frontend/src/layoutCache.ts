import { api } from "./api";

export interface HealthSummary {
  version?: string;
  sha?: string | null;
  build_date?: string | null;
}

let jellyfinWebCached: { url: string | null } | null = null;
let jellyfinWebInflight: Promise<{ url: string | null }> | null = null;

let healthCached: HealthSummary | null = null;
let healthInflight: Promise<HealthSummary> | null = null;

export function invalidateJellyfinWeb(): void {
  jellyfinWebCached = null;
  jellyfinWebInflight = null;
}

export async function fetchJellyfinWeb(): Promise<{ url: string | null }> {
  if (jellyfinWebCached) {
    return jellyfinWebCached;
  }
  if (!jellyfinWebInflight) {
    jellyfinWebInflight = api<{ url: string | null }>("/api/v1/integrations/jellyfin/web")
      .then((body) => {
        jellyfinWebCached = body;
        return body;
      })
      .finally(() => {
        jellyfinWebInflight = null;
      });
  }
  return jellyfinWebInflight;
}

export async function fetchHealthSummary(): Promise<HealthSummary> {
  if (healthCached) {
    return healthCached;
  }
  if (!healthInflight) {
    healthInflight = api<HealthSummary>("/api/v1/health")
      .then((body) => {
        healthCached = body;
        return body;
      })
      .finally(() => {
        healthInflight = null;
      });
  }
  return healthInflight;
}
