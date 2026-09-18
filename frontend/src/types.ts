/**
 * The shape of `GET /api/v1/graph/view`, defined once.
 *
 * It was previously declared three times — in `App.tsx`, as `NodeItem` in
 * `HUDPanels.tsx`, and inline in `CytoscapeGraph.tsx` — and the copies had
 * already drifted apart: `entry_point` was `string` in two of them and the
 * server now sends a boolean. Nothing caught it because the directory had no
 * typechecker (#64). One definition means the next API change breaks in one
 * place, at compile time.
 *
 * Mirrors `NodeView` / `EdgeView` / `GraphView` in `src/lineagelens/rest.py`.
 */

export interface GraphNode {
  id: string
  /** Join key back to query answers, which report qualified names (#55). */
  qualified_name: string
  label: string
  kind: string
  /** Null when the parent falls outside the returned slice. */
  parent?: string | null
  lang?: string
  service?: string | null
  file?: string
  line?: number
  entry_point: boolean
  async_: boolean
  is_test: boolean
  exported?: boolean
  deprecated?: boolean
  duplicate_name: boolean
  lines_of_code?: number
  scope?: string

  /**
   * `null` means "not computed", never "false".
   *
   * Reachability verdicts are gated on #49 and resiliency signals on #60. The
   * server sends null and names these in `unsupported`, so a control over them
   * can be disabled rather than shown switched off over data nobody
   * calculated — an empty result and an unanalysed one must not look alike.
   */
  verdict: string | null
  rescue_mechanism: string | null
  rescue_tier: string | null
  has_resiliency_flag: boolean | null
}

export interface GraphEdge {
  id: string
  source: string
  target: string
  kind: string
  resolution: string
  evidence_tier?: string
  evidence_label?: string
}

export interface GraphViewData {
  nodes: GraphNode[]
  edges: GraphEdge[]
  /** Nodes returned after the server's bound, which may be less than the total. */
  returned: number
  total_available: number
  truncated: boolean
  /** The completeness envelope: what this answer does not cover. */
  coverage: Record<string, unknown>
  /** Node fields with no producer. Filters over them are disabled, not silent. */
  unsupported: string[]
}
