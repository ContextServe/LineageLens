import { useState, useEffect } from 'react'
import './App.css'
import type { GraphViewData } from './types'
import { CytoscapeGraph } from './graph/CytoscapeGraph'
import { DetailPanel } from './components/DetailPanel'
import { Toolbar, type VerdictFilter } from './components/Toolbar'
import { HUDPanels, type ScopeFilters } from './components/HUDPanels'

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

  /** Does the server compute this node field, or is it declared unsupported? */
  function supports(field: string): boolean {
    return !(graphData?.unsupported ?? []).includes(field)
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
      // A filter over an unsupported field is not applied at all. Applying it
      // would hide nothing while looking like it had hidden everything
      // matching — indistinguishable from "there are none" (#64).
      if (legendFilters.entry_point === false && node.entry_point) passesLegendFilter = false
      if (legendFilters.duplicate === false && node.duplicate_name) passesLegendFilter = false
      if (supports('has_resiliency_flag')) {
        if (legendFilters.risk === false && node.has_resiliency_flag) passesLegendFilter = false
      }
      if (supports('verdict')) {
        for (const verdict of ['dead', 'probably_dead', 'test_only', 'dynamic_only']) {
          if (legendFilters[verdict] === false && node.verdict === verdict) {
            passesLegendFilter = false
          }
        }
      }

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

    // Spread the original so `truncated`, `coverage` and `unsupported` survive
    // filtering. Client-side filtering does not change what the server could
    // not compute, and dropping the envelope here would make a filtered view
    // look more complete than the answer it came from.
    return {
      ...graphData,
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
