import type { Core, NodeSingular } from 'cytoscape'

/**
 * Lineage highlighting for the graph view.
 *
 * Imported by `CytoscapeGraph` and missing from the tree — the module was
 * referenced but never committed, so the frontend could not have compiled even
 * if it had a build configuration (#64). The two class names below are the ones
 * `CytoscapeGraph` already styles: `highlighted` and `faded`.
 */

/**
 * Emphasise a set of nodes and the edges between them, fading everything else.
 *
 * Takes node **ids**, not qualified names. The graph is keyed by content hash
 * and query routes answer in qualified names, so the caller maps between them
 * via each node's `qualified_name` before getting here.
 *
 * Edges are highlighted only when *both* endpoints are in the set. An edge with
 * one foot outside the lineage is not part of the lineage, and dimming it keeps
 * the highlighted subgraph readable as a path rather than a halo.
 */
export function highlightLineage(cy: Core, reachableIds: Set<string>): void {
  // One batch: without it Cytoscape re-renders per class change, which on a
  // two-thousand-node graph is visibly slow.
  cy.batch(() => {
    clearHighlight(cy)

    if (reachableIds.size === 0) return

    cy.nodes().forEach((node: NodeSingular) => {
      // Compound parents are containers, not participants. Fading them would
      // punch holes in the layout, so they are left neutral.
      if (node.isParent()) return
      node.addClass(reachableIds.has(node.id()) ? 'highlighted' : 'faded')
    })

    cy.edges().forEach((edge) => {
      const inLineage =
        reachableIds.has(edge.source().id()) && reachableIds.has(edge.target().id())
      edge.addClass(inLineage ? 'highlighted' : 'faded')
    })
  })
}

/** Remove every highlight class, restoring the neutral view. */
export function clearHighlight(cy: Core): void {
  cy.batch(() => {
    cy.elements().removeClass('highlighted faded')
  })
}
