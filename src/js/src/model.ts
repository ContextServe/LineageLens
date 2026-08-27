export const SCHEMA_VERSION = 2;

export interface Evidence {
  tier: "deterministic_fact" | "deterministic_heuristic" | "probabilistic";
  label: string;
  confidence?: number | null;
}

export interface ResiliencySignal {
  category: string;
  severity: "info" | "review" | "high";
  evidence: Evidence;
  line: number;
}

export interface Container {
  id: string;
  kind: "package" | "module";
  name: string;
  file: string | null;
  parent: string | null;
  children: string[];
  docstring?: string | null;
}

export interface SymbolInput {
  name: string;
  type: string;
  default: string | null;
}

export interface SymbolOutput {
  type: string;
  evidence: string;
}

export interface Symbol {
  id: string;
  kind: "class" | "interface" | "function" | "method" | "variable" | "type_alias";
  name: string;
  file: string;
  line: number;
  end_line?: number | null;
  module: string;
  parent: string | null;
  async_: boolean;
  description?: string | null;
  inputs: SymbolInput[];
  outputs: SymbolOutput[];
  decorators: string[];
  bases: string[];
  entry_point_kinds: string[];
  entry_point?: string | null;
  is_abstract: boolean;
  resiliency: ResiliencySignal[];
}

export interface Relation {
  source: string;
  target: string;
  kind: "CALLS" | "AWAIT_CALLS" | "INHERITS" | "OVERRIDES" | "DECORATES" | "EXPORTS" | "IMPORTS";
  file: string;
  line: number;
  evidence: Evidence;
  resolution: "resolved" | "resolved_via_inference" | "external_or_dynamic";
  resolution_evidence: Evidence;
  arguments: Record<string, any>[];
}

export interface CodeGraph {
  schema_version: number;
  project_root: string;
  symbols: Symbol[];
  containers: Container[];
  relations: Relation[];
}
