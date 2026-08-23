import React, { useEffect, useRef } from 'react'
import CytoscapeLib from 'cytoscape'
// @ts-ignore - cytoscape-fcose doesn't have TS types
import FCose from 'cytoscape-fcose'
import { highlightLineage, clearHighlight } from './highlight'

CytoscapeLib.use(FCose)

interface CytoscapeGraphProps {
  data: any
  selectedSymbol?: string | null
  onSelectSymbol?: (id: string) => void
}

export function CytoscapeGraph({ data, selectedSymbol, onSelectSymbol }: CytoscapeGraphProps) {
  const containerRef = useRef<HTMLDivElement>(null)
  const cyRef = useRef<CytoscapeLib.Core | null>(null)

  useEffect(() => {
    if (!containerRef.current || !data) return

    // Convert API data to Cytoscape format
    const elements = [
      ...data.nodes.map((node: any) => ({
        data: {
          id: node.id,
          label: node.label,
          kind: node.kind,
          parent: node.parent,
          entry_point: node.entry_point,
          async_: node.async_,
          has_risk: node.has_resiliency_flag,
          possibly_dead: node.possibly_dead,
          duplicate_name: node.duplicate_name,
        },
      })),
      ...data.edges.map((edge: any, idx: number) => ({
        data: {
          id: `edge-${idx}`,
          source: edge.source,
          target: edge.target,
          kind: edge.kind,
          resolution: edge.resolution,
        },
      })),
    ]

    // Initialize Cytoscape
    const cy = CytoscapeLib({
      container: containerRef.current,
      elements,
      style: [
        {
          selector: 'node',
          style: {
            'content': 'data(label)',
            'text-valign': 'center',
            'text-halign': 'center',
            'background-color': (ele: any) => {
              // Priority: entry_point > has_risk > possibly_dead > default
              if (ele.data('entry_point')) return '#3b82f6'
              if (ele.data('has_risk')) return '#f59e0b'
              if (ele.data('possibly_dead')) return '#ef4444'
              return '#6b7280'
            },
            'border-width': (ele: any) => {
              // Add border for duplicate names
              return ele.data('duplicate_name') ? 3 : 1
            },
            'border-color': (ele: any) => {
              return ele.data('duplicate_name') ? '#8b5cf6' : '#4b5563'
            },
            'width': '60px',
            'height': '60px',
            'font-size': '11px',
            'color': '#fff',
            'text-opacity': 1,
            'text-wrap': 'wrap',
          },
        },
        {
          selector: 'node:parent',
          style: {
            'content': 'data(label)',
            'text-valign': 'top',
            'text-halign': 'left',
            'text-margin-y': 4,
            'background-color': '#e5e7eb',
            'background-opacity': 0.5,
            'border-width': 2,
            'border-color': '#9ca3af',
            'font-size': '12px',
            'color': '#6b7280',
            'text-opacity': 1,
            'padding': '6px',
          },
        },
        {
          selector: 'node:selected',
          style: {
            'border-width': 3,
            'border-color': '#ef4444',
          },
        },
        {
          selector: 'edge',
          style: {
            'target-arrow-shape': 'triangle',
            'line-color': '#d1d5db',
            'target-arrow-color': '#d1d5db',
            'width': 2,
          },
        },
        {
          selector: 'edge.highlighted',
          style: {
            'line-color': '#3b82f6',
            'target-arrow-color': '#3b82f6',
            'width': 3,
          },
        },
        {
          selector: 'node.faded',
          style: {
            'opacity': 0.2,
          },
        },
        {
          selector: 'edge.faded',
          style: {
            'opacity': 0.1,
          },
        },
        {
          selector: 'node.highlighted',
          style: {
            'opacity': 1,
            'border-width': 2,
            'border-color': '#3b82f6',
          },
        },
      ],
      layout: {
        name: 'fcose',
        randomize: false,
        animationDuration: 500,
      } as any,
    })

    cyRef.current = cy

    // Click to select and highlight full lineage
    cy.on('tap', 'node', async (evt: any) => {
      const nodeId = evt.target.id()
      onSelectSymbol?.(nodeId)

      // Fetch full transitive lineage (backward and forward)
      try {
        const [backwardRes, forwardRes] = await Promise.all([
          fetch(`/api/v1/symbols/${encodeURIComponent(nodeId)}/lineage?direction=backward&max_depth=9999`),
          fetch(`/api/v1/symbols/${encodeURIComponent(nodeId)}/lineage?direction=forward&max_depth=9999`),
        ])

        const backwardSteps = await backwardRes.json()
        const forwardSteps = await forwardRes.json()

        // Collect all reachable node IDs
        const reachableIds = new Set<string>([nodeId])
        backwardSteps.forEach((step: any) => reachableIds.add(step.symbol_id))
        forwardSteps.forEach((step: any) => reachableIds.add(step.symbol_id))

        highlightLineage(cy, reachableIds)
      } catch (err) {
        console.error('Failed to fetch lineage:', err)
      }
    })

    // Click on empty area to clear highlighting
    cy.on('tap', (evt: any) => {
      if (evt.target === cy) {
        clearHighlight(cy)
      }
    })

    return () => {
      cy.destroy()
    }
  }, [data, onSelectSymbol])

  // Highlight selected path
  useEffect(() => {
    if (!cyRef.current || !selectedSymbol) return

    const cy = cyRef.current
    cy.$('edge').removeClass('highlighted')
    cy.$(`node[id = "${selectedSymbol}"]`).select()
    cy.center(cy.$(`node[id = "${selectedSymbol}"]`))
  }, [selectedSymbol])

  return <div ref={containerRef} style={{ width: '100%', height: '100%' }} />
}
