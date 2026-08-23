import CytoscapeLib from 'cytoscape'

/**
 * Highlight a full lineage path (all reachable nodes/edges) in the Cytoscape graph.
 * Fades everything not on the path.
 *
 * @param cy Cytoscape core instance
 * @param reachableIds Set of node IDs that are reachable via the full lineage
 */
export function highlightLineage(
  cy: CytoscapeLib.Core,
  reachableIds: Set<string>
) {
  // Clear any existing highlights
  cy.$('node, edge').removeClass('highlighted faded')

  // Collect all edges whose both endpoints are in the reachable set
  const highlightedEdgeIds = new Set<string>()
  cy.edges().forEach((edge) => {
    const source = edge.source().id()
    const target = edge.target().id()

    // Only highlight if both endpoints are in the reachable set
    if (reachableIds.has(source) && reachableIds.has(target)) {
      highlightedEdgeIds.add(edge.id())
    }
  })

  // Apply highlighting
  cy.nodes().forEach((node) => {
    if (reachableIds.has(node.id())) {
      node.addClass('highlighted')
    } else {
      node.addClass('faded')
    }
  })

  cy.edges().forEach((edge) => {
    if (highlightedEdgeIds.has(edge.id())) {
      edge.addClass('highlighted')
    } else {
      edge.addClass('faded')
    }
  })
}

/**
 * Clear all highlights and fading from the graph.
 */
export function clearHighlight(cy: CytoscapeLib.Core) {
  cy.$('node, edge').removeClass('highlighted faded')
}
