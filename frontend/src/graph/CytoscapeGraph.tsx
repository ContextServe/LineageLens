import { useEffect, useRef } from 'react'
import CytoscapeLib from 'cytoscape'
// @ts-ignore - cytoscape-fcose doesn't have TS types
import FCose from 'cytoscape-fcose'
import { highlightLineage, clearHighlight } from './highlight'
import type { GraphViewData } from '../types'
import { ScopeFilters } from '../components/HUDPanels'

CytoscapeLib.use(FCose)

interface CytoscapeGraphProps {
  data: GraphViewData | null
  selectedSymbol?: string | null
  onSelectSymbol?: (id: string) => void
  filters?: ScopeFilters
}

export function CytoscapeGraph({ data, selectedSymbol, onSelectSymbol, filters }: CytoscapeGraphProps) {
  const containerRef = useRef<HTMLDivElement>(null)
  const cyRef = useRef<CytoscapeLib.Core | null>(null)
  const tooltipRef = useRef<HTMLDivElement>(null)

  // Listen for custom fit command from Toolbar
  useEffect(() => {
    function handleFit() {
      if (cyRef.current) {
        cyRef.current.animate({
          fit: { eles: cyRef.current.elements(':visible'), padding: 30 },
          duration: 300,
        })
      }
    }
    window.addEventListener('cy-fit', handleFit)
    return () => window.removeEventListener('cy-fit', handleFit)
  }, [])

  // Initialize Cytoscape once when data changes
  useEffect(() => {
    if (!containerRef.current || !data) return

    const nodeIds = new Set(data.nodes.map((n: any) => n.id))

    // Calculate elements
    const elements = [
      ...data.nodes.map((node) => {
        const loc = node.lines_of_code || 1
        const size = filters?.sizeByLoc
          ? Math.min(100, Math.max(30, Math.round(Math.sqrt(loc) * 12)))
          : 50

        return {
          data: {
            id: node.id,
            // Query routes answer in qualified names; the graph is keyed by
            // node id. Carrying both is what lets a traversal result be
            // highlighted on the picture.
            qualified_name: node.qualified_name,
            label: node.label,
            kind: node.kind,
            parent: node.parent && nodeIds.has(node.parent) ? node.parent : undefined,
            entry_point: node.entry_point,
            async_: node.async_,
            has_risk: node.has_resiliency_flag,
            verdict: node.verdict,
            rescue_mechanism: node.rescue_mechanism,
            rescue_tier: node.rescue_tier,
            duplicate_name: node.duplicate_name,
            lines_of_code: loc,
            node_size: `${size}px`,
          },
        }
      }),
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

    // Fast, responsive Cytoscape initialization with WebGL texture acceleration
    const cy = CytoscapeLib({
      container: containerRef.current,
      elements,
      hideEdgesOnViewport: true,
      textureOnViewport: true,
      pixelRatio: 'auto',
      style: [
        {
          selector: 'node',
          style: {
            'content': 'data(label)',
            'text-valign': 'center',
            'text-halign': 'center',
            'width': 'data(node_size)',
            'height': 'data(node_size)',
            'background-color': (ele: any) => {
              switch (ele.data('verdict')) {
                case 'dead': return '#ef4444'          // red
                case 'probably_dead': return '#f97316' // amber
                case 'test_only': return '#a855f7'     // purple
                case 'dynamic_only': return '#0ea5e9'  // blue
                case 'public_api': return '#14b8a6'    // teal
              }
              if (ele.data('entry_point')) return '#3b82f6'
              if (ele.data('has_risk')) return '#f59e0b'
              return '#6b7280'
            },
            'border-width': (ele: any) => (ele.data('duplicate_name') ? 3 : 1),
            'border-color': (ele: any) => (ele.data('duplicate_name') ? '#8b5cf6' : '#4b5563'),
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
            'text-margin-y': 6,
            'background-color': '#1f2937',
            'background-opacity': 0.6,
            'border-width': 2,
            'border-color': '#374151',
            'font-size': '12px',
            'color': '#9ca3af',
            'text-opacity': 1,
            // `padding` used to be set here. It is a compound-parent property
            // that Cytoscape's node style type does not accept, so it was
            // silently ignored -- and nothing noticed, because this file had
            // never been typechecked (#64). Dropped rather than replaced:
            // the compound layout already spaces parents.
          },
        },
        {
          selector: 'node:selected',
          style: {
            'border-width': 4,
            'border-color': '#ef4444',
          },
        },
        {
          selector: 'edge',
          style: {
            'target-arrow-shape': 'triangle',
            'line-color': '#4b5563',
            'target-arrow-color': '#4b5563',
            'width': 1.5,
            'curve-style': 'straight',
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
            'opacity': 0.15,
          },
        },
        {
          selector: 'edge.faded',
          style: {
            'opacity': 0.05,
          },
        },
        {
          selector: 'node.highlighted',
          style: {
            'opacity': 1,
            'border-width': 3,
            'border-color': '#3b82f6',
          },
        },
      ],
      layout: {
        name: 'fcose',
        quality: 'default',
        randomize: true,
        animate: false,
        fit: true,
        padding: 40,
        nodeDimensionsIncludeLabels: true,
        uniformNodeDimensions: false,
        packComponents: true,
        nodeRepulsion: 4500,
        idealEdgeLength: 60,
        edgeElasticity: 0.45,
        nestingFactor: 0.1,
        gravity: 0.25,
        numIter: 2500,
        tilingPaddingVertical: 20,
        tilingPaddingHorizontal: 20,
      } as any,
    })

    cy.resize()
    cy.fit(undefined, 40)

    cyRef.current = cy

    // Hover Tooltip Events
    cy.on('mouseover', 'node', (evt: any) => {
      const node = evt.target
      const tooltip = tooltipRef.current
      if (!tooltip) return

      const pos = evt.renderedPosition
      tooltip.innerHTML = `
        <div style="font-weight: bold; margin-bottom: 4px; color: #60a5fa;">${node.data('id')}</div>
        <div>Kind: <strong>${node.data('kind')}</strong></div>
        <div>LOC: <strong>${node.data('lines_of_code')}</strong></div>
        ${node.data('verdict') ? `<div>Verdict: <strong>${node.data('verdict')}</strong></div>` : ''}
        ${node.data('has_risk') ? `<div style="color: #f59e0b;">⚠️ Risk signal detected</div>` : ''}
      `
      tooltip.style.left = `${pos.x + 15}px`
      tooltip.style.top = `${pos.y + 15}px`
      tooltip.style.opacity = '1'
    })

    cy.on('mouseout', 'node', () => {
      if (tooltipRef.current) {
        tooltipRef.current.style.opacity = '0'
      }
    })

    // Click to select and highlight lineage (max_depth=10 for ultra-fast response)
    cy.on('tap', 'node', async (evt: any) => {
      const nodeId = evt.target.id()
      onSelectSymbol?.(nodeId)

      try {
        const id = encodeURIComponent(nodeId)
        const [backwardRes, forwardRes] = await Promise.all([
          fetch(`/api/v1/symbols/${id}/lineage?direction=backward&max_depth=10`),
          fetch(`/api/v1/symbols/${id}/lineage?direction=forward&max_depth=10`),
        ])

        // Each result is a *path* with a hop list, not a flat reachability
        // set: schema 3 returned a bag whose depths were DFS artefacts, so no
        // chain could be reconstructed from it. Hops report `node` as a
        // qualified name; the graph is keyed by node id, so map back through
        // `qualified_name` on the node view.
        const byQualifiedName = new Map<string, string>()
        cy.nodes().forEach((n: any) => {
          const qn = n.data('qualified_name')
          if (qn) byQualifiedName.set(qn, n.id())
        })

        const reachableIds = new Set<string>([nodeId])
        for (const response of [backwardRes, forwardRes]) {
          if (!response.ok) continue
          const body = await response.json()
          for (const path of body.results ?? []) {
            for (const hop of path.hops ?? []) {
              const mapped = byQualifiedName.get(hop.node)
              if (mapped) reachableIds.add(mapped)
            }
          }
        }

        highlightLineage(cy, reachableIds)
      } catch (err) {
        console.error('Failed to fetch lineage:', err)
      }
    })

    cy.on('tap', (evt: any) => {
      if (evt.target === cy) {
        clearHighlight(cy)
      }
    })

    return () => {
      cy.destroy()
    }
  }, [data])

  // Update node sizing dynamically without destroying Cytoscape instance
  useEffect(() => {
    if (!cyRef.current || !data) return
    const cy = cyRef.current

    cy.batch(() => {
      cy.nodes().forEach(node => {
        const loc = node.data('lines_of_code') || 1
        const size = filters?.sizeByLoc
          ? Math.min(100, Math.max(30, Math.round(Math.sqrt(loc) * 12)))
          : 50
        node.style('width', `${size}px`)
        node.style('height', `${size}px`)
      })
    })
  }, [filters?.sizeByLoc, data])

  // Apply display scope filters (Show Modules, Classes, Functions, External)
  useEffect(() => {
    if (!cyRef.current || !filters) return
    const cy = cyRef.current

    cy.batch(() => {
      cy.nodes().forEach(node => {
        const kind = node.data('kind')
        let show = true

        if ((kind === 'module' || kind === 'package') && !filters.showModules) show = false
        if (kind === 'class' && !filters.showClasses) show = false
        if ((kind === 'function' || kind === 'method') && !filters.showFunctions) show = false
        if (kind === 'external' && !filters.showExternal) show = false

        if (show) {
          node.style('display', 'element')
        } else {
          node.style('display', 'none')
        }
      })
    })
  }, [filters])

  // Center & highlight on selected symbol
  useEffect(() => {
    if (!cyRef.current || !selectedSymbol) return
    const cy = cyRef.current

    const targetNode = cy.$(`node[id = "${selectedSymbol}"]`)
    if (targetNode.length > 0) {
      cy.$('edge').removeClass('highlighted')
      targetNode.select()
      cy.animate({
        center: { eles: targetNode },
        zoom: 1.5,
        duration: 300,
      })
    }
  }, [selectedSymbol])

  return (
    <div style={{ position: 'relative', width: '100%', height: '100%' }}>
      <div ref={containerRef} style={{ width: '100%', height: '100%' }} />
      <div ref={tooltipRef} className="cy-tooltip" style={{ opacity: 0 }} />
    </div>
  )
}
