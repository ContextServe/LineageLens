import React, { useState, useEffect } from 'react'
import './App.css'
import { CytoscapeGraph } from './graph/CytoscapeGraph'
import { SearchPanel } from './components/SearchPanel'
import { DetailPanel } from './components/DetailPanel'
import { Toolbar } from './components/Toolbar'

interface GraphViewData {
  nodes: Array<{
    id: string
    label: string
    kind: string
    parent?: string
    entry_point?: string
    async_: boolean
    has_resiliency_flag: boolean
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
          <Toolbar onRefresh={fetchGraph} />
          {graphData && (
            <CytoscapeGraph
              data={graphData}
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
