export type MetroLevel = 1 | 2 | 3;

export type EdgeKind = "declenche" | "alimente" | "attend" | "bloque";

export interface MetroGraphStation {
  id: string;
  line: string;
  line_number: number;
  label: string;
  color: string;
  x: number;
  y: number;
  label_dy?: number;
  label_size?: number;
  label_anchor?: "start" | "middle" | "end";
  label_dx?: number;
  instrumented: boolean;
}

export interface MetroGraphEdge {
  id: string;
  origin: string;
  dest: string;
  kind: EdgeKind;
  untraceable: boolean;
  proof?: string | null;
}

export interface MetroGraphLine {
  number: number;
  name: string;
  color: string;
  x: number;
  y: number;
  focus_station_id?: string | null;
}

export interface MetroGraphBody {
  view_box: { width: number; height: number };
  station_count_total: number;
  station_count_visible: number;
  edge_count_visible: number;
  untraceable_count: number;
  lines: MetroGraphLine[];
  stations: MetroGraphStation[];
  edges: MetroGraphEdge[];
  level1_edges: { from: string; to: string; count_label: string }[];
  diagnostics: MetroDiagnostic[];
  rng5_mechanisms: MetroMechanism[];
  rng5_exits: MetroExit[];
  peer_off_plan: { x: number; y: number; label: string };
  ruche_ring: { cx: number; cy: number; r: number };
  retention_days: number;
}

export interface MetroDiagnostic {
  id: string;
  tag: string;
  tag_class: string;
  title: string;
  detail: string;
  code: string;
  edge_id?: string;
  station_id?: string;
}

export interface MetroMechanism {
  id: string;
  label: string;
  code: string;
  refs: string;
  pole?: boolean;
}

export interface MetroExit {
  order: number;
  edge_id: string;
  dest: string;
  line_color: string;
  display_dest?: string;
  anomaly?: string;
  untraceable_ref?: string;
}

export interface MetroActivityBody {
  hours: number;
  since: string;
  until: string;
  has_data: boolean;
  collection: {
    active: boolean;
    until: string | null;
    mode: string;
    duration: string | null;
  };
  kpis: {
    trains_in_progress: number;
    active_blocks: number;
    slow_stations: number;
    network_anomalies: number;
    edge_passes_24h: number;
    median_pass_s: number | null;
    untraceable_links: number;
  };
  active_blocks: {
    station_id: string;
    blocked_by: string | null;
    edge_id: string | null;
    since: string;
    minutes: number;
  }[];
  slow_stations: { station_id: string; median_ms: number; baseline_ms: number }[];
  station_stats: Record<
    string,
    {
      passes_24h: number;
      median_ms: number | null;
      baseline_7d_ms: number | null;
      slow: boolean;
      frozen: boolean;
      last_at: string | null;
      last_subject_ref: string | null;
    }
  >;
  edge_hits: Record<string, number>;
  lit_edges: string[];
  lit_pass_edge: string | null;
  level1_edges: { from: string; to: string; count: number; count_label: string }[];
  edge_details: Record<string, unknown>;
  station_details: Record<string, { outbound: unknown[] }>;
  rng5_exit_stats: Record<string, { passes_24h: number; median_ms: number | null }>;
  graph: MetroGraphBody;
}