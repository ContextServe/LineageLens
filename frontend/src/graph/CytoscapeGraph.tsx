import React, { useEffect, useRef } from 'react'
import CytoscapeLib from 'cytoscape'
// @ts-ignore - cytoscape-fcose doesn't have TS types
import FCose from 'cytoscape-fcose'
import { highlightFlow, clearHighlight } from './highlight'

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
              if (ele.data('entry_point')) return '#3b82f6'
              if (ele.data('has_risk')) return '#f59e0b'
              return '#6b7280'
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
            'background-color': '#e5e7eb',
            'background-opacity': 0.5,
            'border-width': 2,
            'border-color': '#9ca3af',
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

    // Click to select and highlight flow
    cy.on('tap', 'node', async (evt: any) => {
      const nodeId = evt.target.id()
      onSelectSymbol?.(nodeId)

      // Fetch callers and callees to highlight the flow
      try {
        const [callersRes, calleesRes] = await Promise.all([
          fetch(`/api/v1/symbols/${encodeURIComponent(nodeId)}/callers`),
          fetch(`/api/v1/symbols/${encodeURIComponent(nodeId)}/callees`),
        ])

        const callers = await callersRes.json()
        const callees = await calleesRes.json()

        const callerIds = callers.map((rel: any) => rel.source)
        const calleeIds = callees.map((rel: any) => rel.target)

        highlightFlow(cy, nodeId, callerIds, calleeIds)
      } catch (err) {
        console.error('Failed to fetch flow:', err)
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
