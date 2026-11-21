import type { HivePeerLoadResponse } from "../hive/hivePeerLoad";
import { summarizePeerLoad } from "../hive/hivePeerLoad";
import { formatParis } from "../time";

export type ComponentRow = {
  name: string;
  status: string;
  message: string;
};

export type SourceTargetRow = {
  key: string;
  kind: string;
  label: string;
  state: string;
  message: string;
  since: string;
  last_debrid_at?: string | null;
};

export type UpdateSnapshot = {
  detail: string;
  updateAvailable: boolean;
  watchtowerConfigured: boolean;
  checkedAtLabel: string;
};

export type HealthServiceKind = "component" | "source" | "hive" | "update";

export type HealthService = {
  id: string;
  kind: HealthServiceKind;
  label: string;
  status: string;
  message: string;
  componentName?: string;
  sourceKey?: string;
};

export const COMPONENT_LABELS: Record<string, string> = {
  aria2: "aria2",
  alldebrid: "AllDebrid",
  tmdb: "TMDB",
  jellyfin: "Jellyfin",
  wawacity: "Wawacity",
  notifier: "Notifications",
  disk: "Espace disque",
  library: "Bibliothèque",
  volumes: "Volumes",
};

export const STATE_LABELS: Record<string, string> = {
  ok: "en ligne",
  down: "indisponible",
  degraded: "dégradé",
  skipped: "non configuré",
};

const ORDER_BEFORE_SOURCES = ["aria2", "alldebrid", "tmdb", "jellyfin", "wawacity"] as const;
const ORDER_AFTER_SOURCES = ["hive", "disk", "library", "notifier", "update"] as const;

function sourceAsService(row: SourceTargetRow): HealthService {
  return {
    id: `source:${row.key}`,
    kind: "source",
    label: row.label,
    status: row.state === "down" ? "down" : row.state === "ok" ? "ok" : row.state,
    message: row.message.trim() || `Depuis ${formatParis(row.since)}.`,
    sourceKey: row.key,
  };
}

function componentAsService(row: ComponentRow): HealthService {
  return {
    id: row.name,
    kind: "component",
    label: COMPONENT_LABELS[row.name] || row.name,
    status: row.status,
    message: row.message,
    componentName: row.name,
  };
}

function buildHiveService(
  hiveEnabled: boolean,
  payload: HivePeerLoadResponse | null,
  error: string,
): HealthService | null {
  if (!hiveEnabled) {
    return null;
  }
  if (error) {
    return {
      id: "hive",
      kind: "hive",
      label: "Ruche",
      status: "down",
      message: error,
    };
  }
  if (!payload?.self) {
    return {
      id: "hive",
      kind: "hive",
      label: "Ruche",
      status: "degraded",
      message: "Charge des pairs indisponible.",
    };
  }
  const summary = summarizePeerLoad(payload.self, payload.items, payload.self.instance_id);
  const sends =
    summary.sendsLabel !== "—" && payload.self.out_active > 0
      ? `${summary.reachable} pair${summary.reachable > 1 ? "s" : ""} joignable${summary.reachable > 1 ? "s" : ""} · ${payload.self.out_active} envoi en cours`
      : `${summary.reachable} pair${summary.reachable > 1 ? "s" : ""} joignable${summary.reachable > 1 ? "s" : ""} · aucun envoi en cours`;
  return {
    id: "hive",
    kind: "hive",
    label: "Ruche",
    status: summary.reachable > 0 || summary.unknown === 0 ? "ok" : "degraded",
    message: sends,
  };
}

function buildUpdateService(snapshot: UpdateSnapshot | null): HealthService {
  if (!snapshot) {
    return {
      id: "update",
      kind: "update",
      label: "Mise à jour",
      status: "degraded",
      message: "État de mise à jour indisponible.",
    };
  }
  const parts: string[] = [];
  if (snapshot.updateAvailable) {
    parts.push("Mise à jour disponible.");
  } else {
    parts.push("À jour.");
  }
  if (snapshot.watchtowerConfigured) {
    parts.push("Watchtower est configuré.");
  } else {
    parts.push("Watchtower n'est pas configuré.");
  }
  if (snapshot.checkedAtLabel) {
    parts.push(`Vérifié ${snapshot.checkedAtLabel}.`);
  }
  let status = "ok";
  if (snapshot.updateAvailable) {
    status = "degraded";
  } else if (!snapshot.watchtowerConfigured) {
    status = "skipped";
  }
  return {
    id: "update",
    kind: "update",
    label: "Mise à jour",
    status,
    message: parts.join(" "),
  };
}

export function buildHealthServices(input: {
  components: ComponentRow[];
  sources: SourceTargetRow[];
  hiveEnabled: boolean;
  hivePayload: HivePeerLoadResponse | null;
  hiveError: string;
  update: UpdateSnapshot | null;
}): HealthService[] {
  const byName = new Map(input.components.map((row) => [row.name, row]));
  const sourceOnly = input.sources.filter((row) => row.kind === "source");

  const ordered: HealthService[] = [];

  for (const name of ORDER_BEFORE_SOURCES) {
    const row = byName.get(name);
    if (row) {
      ordered.push(componentAsService(row));
    }
  }

  for (const row of sourceOnly) {
    if (row.label.toLowerCase() === "wawacity") {
      continue;
    }
    ordered.push(sourceAsService(row));
  }

  const hive = buildHiveService(input.hiveEnabled, input.hivePayload, input.hiveError);
  for (const token of ORDER_AFTER_SOURCES) {
    if (token === "hive") {
      if (hive) {
        ordered.push(hive);
      }
      continue;
    }
    if (token === "update") {
      ordered.push(buildUpdateService(input.update));
      continue;
    }
    const row = byName.get(token);
    if (row) {
      ordered.push(componentAsService(row));
    }
  }

  const volumes = byName.get("volumes");
  if (volumes && volumes.status !== "ok") {
    ordered.push(componentAsService(volumes));
  }

  return ordered;
}

export function partitionServices(services: HealthService[]): {
  problems: HealthService[];
  healthy: HealthService[];
  skipped: HealthService[];
} {
  const skipped = services.filter((row) => row.status === "skipped");
  const active = services.filter((row) => row.status !== "skipped");
  const problems = active.filter((row) => row.status === "down" || row.status === "degraded");
  const healthy = active.filter((row) => row.status === "ok");
  return { problems, healthy, skipped };
}

export type VerdictCopy = {
  title: string;
  rail: "ok" | "warn" | "down";
  lines: string[];
};

export function buildVerdict(services: HealthService[]): VerdictCopy {
  const { problems, healthy, skipped } = partitionServices(services);
  const downs = problems.filter((row) => row.status === "down");
  const warns = problems.filter((row) => row.status === "degraded");

  if (!problems.length) {
    return {
      title: "Tout va bien",
      rail: "ok",
      lines: ["Tous les services configurés répondent."],
    };
  }

  const titleParts: string[] = [];
  if (downs.length) {
    titleParts.push(`${downs.length} panne${downs.length > 1 ? "s" : ""}`);
  }
  if (warns.length) {
    titleParts.push(`${warns.length} alerte${warns.length > 1 ? "s" : ""}`);
  }
  const rail: VerdictCopy["rail"] = downs.length ? "down" : "warn";
  const lines = problems.slice(0, 2).map((row) => `${row.label} : ${row.message}`);
  if (lines.length === 0 && skipped.length) {
    lines.push("Certains services ne sont pas configurés.");
  }
  return {
    title: titleParts.join(", "),
    rail,
    lines,
  };
}

export function stateLabel(status: string, serviceId?: string): string {
  if (serviceId === "update" && status === "ok") {
    return "à jour";
  }
  return STATE_LABELS[status] || status;
}

export function pillClass(status: string): string {
  if (status === "ok") {
    return "health-pill health-pill-ok";
  }
  if (status === "degraded") {
    return "health-pill health-pill-warn";
  }
  if (status === "down") {
    return "health-pill health-pill-down";
  }
  return "health-pill health-pill-muted";
}
