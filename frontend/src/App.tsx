import React, { useState, useEffect } from 'react'
import './App.css'
import { CytoscapeGraph } from './graph/CytoscapeGraph'
import { SearchPanel } from './components/SearchPanel'
import { DetailPanel } from './components/DetailPanel'
import { Toolbar, type VerdictFilter } from './components/Toolbar'

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

  function getFilteredGraphData(): GraphViewData | null {
    if (!graphData) return null

    // Determine which nodes to keep based on test filter
    const nodesToKeep = new Set<string>()
    const nodeIsTest = new Map<string, boolean>()

    for (const node of graphData.nodes) {
      nodeIsTest.set(node.id, node.is_test)

      const passesTestFilter =
        testFilter === 'all' ||
        (testFilter === 'source' && !node.is_test) ||
        (testFilter === 'tests' && node.is_test)

      // Containers have no verdict of their own, so they survive a verdict filter
      // in order to keep the compound hierarchy intact around the nodes that match.
      const passesVerdictFilter =
        verdictFilter === 'all' || !node.verdict || node.verdict === verdictFilter

      if (passesTestFilter && passesVerdictFilter) {
        nodesToKeep.add(node.id)
      }
    }

    // Filter nodes and clean up parent refs
    const filteredNodes = graphData.nodes
      .filter(node => nodesToKeep.has(node.id))
      .map(node => ({
        ...node,
        parent: node.parent && nodesToKeep.has(node.parent) ? node.parent : undefined,
      }))

    // Filter edges: only keep edges where both endpoints exist in filtered nodes
    const filteredEdges = graphData.edges.filter(
      edge => nodesToKeep.has(edge.source) && nodesToKeep.has(edge.target)
    )

    return {
      nodes: filteredNodes,
      edges: filteredEdges,
    }
  }

  if (loading) {
    return <div className="app loading">Loading codebase graph...</div>
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

  return (
    <div className="app">
      <header>
        <h1>LineageLens</h1>
        <p>Evidence-labelled Python code lineage</p>
      </header>

      <div className="layout">
        <aside className="sidebar left">
          <SearchPanel onSelectSymbol={setSelectedSymbol} />
        </aside>

        <main className="graph-container">
          <Toolbar
            onRefresh={fetchGraph}
            testFilter={testFilter}
            onTestFilterChange={setTestFilter}
            verdictFilter={verdictFilter}
            onVerdictFilterChange={setVerdictFilter}
          />
          {graphData && (
            <CytoscapeGraph
              data={getFilteredGraphData()!}
              selectedSymbol={selectedSymbol}
              onSelectSymbol={setSelectedSymbol}
            />
          )}
        </main>

        <aside className="sidebar right">
          {selectedSymbol ? (
            <DetailPanel symbolId={selectedSymbol} />
          ) : (
            <div className="no-selection">
              <p>Select a symbol to view details</p>
            </div>
          )}
        </aside>
      </div>
    </div>
  )
}

export default App
