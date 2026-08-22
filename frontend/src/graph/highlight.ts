import CytoscapeLib from 'cytoscape'

/**
 * Highlight a flow path (caller → target → callees) in the Cytoscape graph.
 * Fades everything not on the path.
 */
export function highlightFlow(
  cy: CytoscapeLib.Core,
  symbolId: string,
  callerIds: string[],
  calleeIds: string[]
) {
  // Clear any existing highlights
  cy.$('node, edge').removeClass('highlighted faded')

  // Collect all nodes to highlight: the symbol itself + its callers + its callees
  const highlightedNodeIds = new Set([symbolId, ...callerIds, ...calleeIds])

  // Collect all edges to highlight:
  // - edges FROM callers TO symbolId
  // - edges FROM symbolId TO callees
  const highlightedEdgeIds = new Set<string>()
  cy.edges().forEach((edge) => {
    const source = edge.source().id()
    const target = edge.target().id()

    if ((callerIds.includes(source) && target === symbolId) || (source === symbolId && calleeIds.includes(target))) {
      highlightedEdgeIds.add(edge.id())
    }
  })

  // Apply highlighting
  cy.nodes().forEach((node) => {
    if (highlightedNodeIds.has(node.id())) {
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
