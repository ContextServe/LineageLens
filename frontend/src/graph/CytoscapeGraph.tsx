import React, { useEffect, useRef } from 'react'
import CytoscapeLib from 'cytoscape'
import FCose from 'cytoscape-fcose'

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
      ],
      layout: {
        name: 'fcose',
        quality: 'default',
        randomize: false,
        animationDuration: 500,
      },
    })

    cyRef.current = cy

    // Click to select
    cy.on('tap', 'node', (evt: any) => {
      const nodeId = evt.target.id()
      onSelectSymbol?.(nodeId)
    })

    // Highlight on select
    cy.on('tap', (evt: any) => {
      if (evt.target === cy) {
        cy.$('edge').removeClass('highlighted')
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
