import React, { useState, useEffect } from 'react'
import './App.css'
import { CytoscapeGraph } from './graph/CytoscapeGraph'
import { DetailPanel } from './components/DetailPanel'
import { Toolbar, type VerdictFilter } from './components/Toolbar'
import { HUDPanels, type ScopeFilters } from './components/HUDPanels'

interface GraphViewData {
  nodes: Array<{
    id: string
    label: string
    kind: string
    parent?: string
    entry_point?: string
    async_: boolean
    has_resiliency_flag: boolean
    is_test: boolean
    verdict?: string
    rescue_mechanism?: string
    rescue_tier?: string
    scope?: string
    duplicate_name: boolean
    lines_of_code?: number
  }>
  edges: Array<{
    id: string
    source: string
    target: string
    kind: string
    resolution: string
  }>
}

export function App() {
  const [graphData, setGraphData] = useState<GraphViewData | null>(null)
  const [selectedSymbol, setSelectedSymbol] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [testFilter, setTestFilter] = useState<'all' | 'source' | 'tests'>('all')
  const [verdictFilter, setVerdictFilter] = useState<VerdictFilter>('all')

  const [legendFilters, setLegendFilters] = useState<Record<string, boolean>>({
    entry_point: true,
    risk: true,
    dead: true,
    probably_dead: true,
    test_only: true,
    dynamic_only: true,
    duplicate: true,
  })

  const [filters, setFilters] = useState<ScopeFilters>({
    showModules: true,
    showClasses: true,
    showFunctions: true,
    showExternal: true,
    sizeByLoc: true,
    linkKinds: {},
  })

  // Load graph on mount
  useEffect(() => {
    fetchGraph()
  }, [])

  async function fetchGraph() {
    try {
      setLoading(true)
      const response = await fetch('/api/v1/graph/view')
      if (!response.ok) throw new Error(`HTTP ${response.status}`)
      const data = await response.json()
      setGraphData(data)
      setError(null)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load graph')
    } finally {
      setLoading(false)
    }
  }

  function handleToggleLegendFilter(key: string) {
    setLegendFilters(prev => ({
      ...prev,
      [key]: !prev[key],
    }))
  }

  function getFilteredGraphData(): GraphViewData | null {
    if (!graphData) return null

    const nodesToKeep = new Set<string>()

    for (const node of graphData.nodes) {
      const passesTestFilter =
        testFilter === 'all' ||
        (testFilter === 'source' && !node.is_test) ||
        (testFilter === 'tests' && node.is_test)

      const passesVerdictFilter =
        verdictFilter === 'all' || !node.verdict || node.verdict === verdictFilter

      // Interactive legend filters
      let passesLegendFilter = true
      if (legendFilters.entry_point === false && node.entry_point) passesLegendFilter = false
      if (legendFilters.risk === false && node.has_resiliency_flag) passesLegendFilter = false
      if (legendFilters.dead === false && node.verdict === 'dead') passesLegendFilter = false
      if (legendFilters.probably_dead === false && node.verdict === 'probably_dead') passesLegendFilter = false
      if (legendFilters.test_only === false && node.verdict === 'test_only') passesLegendFilter = false
      if (legendFilters.dynamic_only === false && node.verdict === 'dynamic_only') passesLegendFilter = false
      if (legendFilters.duplicate === false && node.duplicate_name) passesLegendFilter = false

      if (passesTestFilter && passesVerdictFilter && passesLegendFilter) {
        nodesToKeep.add(node.id)
      }
    }

    const filteredNodes = graphData.nodes
      .filter(node => nodesToKeep.has(node.id))
      .map(node => ({
        ...node,
        parent: node.parent && nodesToKeep.has(node.parent) ? node.parent : undefined,
      }))

    const filteredEdges = graphData.edges.filter(
      edge => nodesToKeep.has(edge.source) && nodesToKeep.has(edge.target)
    )

    return {
      nodes: filteredNodes,
      edges: filteredEdges,
    }
  }

  if (loading) {
    return <div className="app loading">Loading LineageLens graph index...</div>
  }

  if (error) {
    return (
      <div className="app error">
        <h2>Failed to load graph</h2>
        <p>{error}</p>
        <button onClick={fetchGraph}>Retry</button>
      </div>
    )
  }

  const activeGraph = getFilteredGraphData()

  return (
    <div className="app">
      <header>
        <h1>LineageLens</h1>
        <p>Real-Time Code Lineage & Visual Analytics</p>
      </header>

      <div className="layout">
        <main className="graph-container">
          <Toolbar
            onRefresh={fetchGraph}
            testFilter={testFilter}
            onTestFilterChange={setTestFilter}
            verdictFilter={verdictFilter}
            onVerdictFilterChange={setVerdictFilter}
            legendFilters={legendFilters}
            onToggleLegendFilter={handleToggleLegendFilter}
            nodes={graphData?.nodes || []}
            onSelectSymbol={setSelectedSymbol}
            selectedSymbol={selectedSymbol}
            onClearSelection={() => setSelectedSymbol(null)}
          />

          {activeGraph && (
            <div className="canvas-wrapper">
              <HUDPanels
                nodes={activeGraph.nodes}
                edges={activeGraph.edges}
                onSelectSymbol={setSelectedSymbol}
                filters={filters}
                onFiltersChange={setFilters}
                selectedSymbol={selectedSymbol}
                onClearSelection={() => setSelectedSymbol(null)}
              />
              <CytoscapeGraph
                data={activeGraph}
                selectedSymbol={selectedSymbol}
                onSelectSymbol={setSelectedSymbol}
                filters={filters}
              />
            </div>
          )}
        </main>

        <aside className="sidebar right">
          {selectedSymbol ? (
            <DetailPanel symbolId={selectedSymbol} />
          ) : (
            <div className="no-selection">
              <p>Select a symbol or module to view contract details, risks, and transitive call paths.</p>
            </div>
          )}
        </aside>
      </div>
    </div>
  )
}

export default App
