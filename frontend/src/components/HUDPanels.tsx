import React, { useState, useEffect } from 'react'

export interface ScopeFilters {
  showModules: boolean
  showClasses: boolean
  showFunctions: boolean
  showExternal: boolean
  sizeByLoc: boolean
  linkKinds: Record<string, boolean>
}

export interface NodeItem {
  id: string
  label: string
  kind: string
  parent?: string
  entry_point?: string
  async_: boolean
  has_resiliency_flag: boolean
  is_test: boolean
  verdict?: string
  scope?: string
  duplicate_name: boolean
  lines_of_code?: number
}

interface HUDPanelsProps {
  nodes: NodeItem[]
  edges: Array<{ id: string; source: string; target: string; kind: string }>
  onSelectSymbol: (id: string) => void
  filters: ScopeFilters
  onFiltersChange: (filters: ScopeFilters) => void
  selectedSymbol: string | null
  onClearSelection: () => void
}

export function HUDPanels({
  nodes,
  edges,
  onSelectSymbol,
  filters,
  onFiltersChange,
  selectedSymbol,
  onClearSelection,
}: HUDPanelsProps) {
  // Collapsible states
  const [collapsedPanels, setCollapsedPanels] = useState<Record<string, boolean>>({
    stats: false,
    legend: true,
    massive: true,
    unlinked: true,
    display: false,
  })

  // Search state
  const [searchQuery, setSearchQuery] = useState('')
  const [searchOpen, setSearchOpen] = useState(false)

  // Massive objects state
  const [locThreshold, setLocThreshold] = useState(50)
  const [massiveKinds, setMassiveKinds] = useState({
    module: true,
    class: true,
    function: true,
    method: true,
  })

  // Unlinked tab state
  const [unlinkedTab, setUnlinkedTab] = useState<'unlinked' | 'dead' | 'risks'>('unlinked')

  // Keyboard shortcut Ctrl+F or Cmd+F for search focus
  useEffect(() => {
    function handleKeyDown(e: KeyboardEvent) {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'f') {
        e.preventDefault()
        const input = document.getElementById('hudSearchInput')
        if (input) input.focus()
      } else if (e.key === 'Escape') {
        onClearSelection()
        setSearchQuery('')
      }
    }
    window.addEventListener('keydown', handleKeyDown)
    return () => window.removeEventListener('keydown', handleKeyDown)
  }, [onClearSelection])

  const togglePanel = (panelKey: string) => {
    setCollapsedPanels(prev => ({ ...prev, [panelKey]: !prev[panelKey] }))
  }

  // Calculate statistics
  const totalModules = nodes.filter(n => n.kind === 'module' || n.kind === 'package').length
  const totalClasses = nodes.filter(n => n.kind === 'class').length
  const totalFunctions = nodes.filter(n => n.kind === 'function' || n.kind === 'method').length
  const totalRisks = nodes.filter(n => n.has_resiliency_flag).length
  const deadCodeCount = nodes.filter(n => n.verdict === 'dead' || n.verdict === 'probably_dead').length

  // Filter search results
  const searchResults = searchQuery.trim().length >= 2
    ? nodes.filter(n => n.id.toLowerCase().includes(searchQuery.toLowerCase()) || n.label.toLowerCase().includes(searchQuery.toLowerCase())).slice(0, 15)
    : []

  // Calculate massive objects
  const massiveObjects = nodes
    .filter(n => (n.lines_of_code || 1) >= locThreshold)
    .filter(n => massiveKinds[n.kind as keyof typeof massiveKinds] ?? true)
    .sort((a, b) => (b.lines_of_code || 1) - (a.lines_of_code || 1))

  // Calculate unlinked nodes (degree 0)
  const connectedNodeIds = new Set<string>()
  edges.forEach(e => {
    connectedNodeIds.add(e.source)
    connectedNodeIds.add(e.target)
  })
  const unlinkedNodes = nodes.filter(n => !n.parent && n.kind !== 'module' && n.kind !== 'package' && !connectedNodeIds.has(n.id))
  const deadCodeNodes = nodes.filter(n => n.verdict === 'dead' || n.verdict === 'probably_dead' || n.verdict === 'test_only')
  const riskNodes = nodes.filter(n => n.has_resiliency_flag)

  return (
    <div className="hud-overlay">
      {/* 1. Top Search Header */}
      <div className="hud-search-bar">
        <div className="search-input-wrapper">
          <input
            id="hudSearchInput"
            type="text"
            className="hud-input"
            placeholder="Search symbols/modules... (Ctrl+F)"
            value={searchQuery}
            onChange={e => {
              setSearchQuery(e.target.value)
              setSearchOpen(true)
            }}
            onFocus={() => setSearchOpen(true)}
          />
          {searchQuery && (
            <button className="clear-btn" onClick={() => setSearchQuery('')}>×</button>
          )}
        </div>

        {selectedSymbol && (
          <div className="selected-badge">
            <span>Target: <strong>{selectedSymbol.split('::').pop()}</strong></span>
            <button onClick={onClearSelection} className="btn-clear-target">Clear Focus (Esc)</button>
          </div>
        )}

        {searchOpen && searchResults.length > 0 && (
          <ul className="search-dropdown">
            {searchResults.map(item => (
              <li
                key={item.id}
                onClick={() => {
                  onSelectSymbol(item.id)
                  setSearchOpen(false)
                }}
              >
                <span className={`kind-tag kind-${item.kind}`}>{item.kind}</span>
                <span className="symbol-id">{item.id}</span>
                {item.lines_of_code && <span className="loc-tag">{item.lines_of_code} LOC</span>}
              </li>
            ))}
          </ul>
        )}
      </div>

      {/* 2. Top-Right Statistics & Legend */}
      <div className={`hud-panel panel-stats ${collapsedPanels.stats ? 'collapsed' : ''}`}>
        <div className="panel-header" onClick={() => togglePanel('stats')}>
          <h4>📊 Statistics</h4>
          <button className="toggle-btn">{collapsedPanels.stats ? '+' : '−'}</button>
        </div>
        {!collapsedPanels.stats && (
          <div className="panel-body grid-stats">
            <div className="stat-item"><span className="stat-num">{totalModules}</span><span className="stat-lbl">Modules</span></div>
            <div className="stat-item"><span className="stat-num">{totalClasses}</span><span className="stat-lbl">Classes</span></div>
            <div className="stat-item"><span className="stat-num">{totalFunctions}</span><span className="stat-lbl">Functions</span></div>
            <div className="stat-item"><span className="stat-num">{edges.length}</span><span className="stat-lbl">Relations</span></div>
            <div className="stat-item danger"><span className="stat-num">{deadCodeCount}</span><span className="stat-lbl">Dead Code</span></div>
            <div className="stat-item warning"><span className="stat-num">{totalRisks}</span><span className="stat-lbl">Risks</span></div>
          </div>
        )}
      </div>

      {/* 3. Bottom-Left Display Scope Toggles */}
      <div className={`hud-panel panel-display ${collapsedPanels.display ? 'collapsed' : ''}`}>
        <div className="panel-header" onClick={() => togglePanel('display')}>
          <h4>⚙️ Display & Layout</h4>
          <button className="toggle-btn">{collapsedPanels.display ? '+' : '−'}</button>
        </div>
        {!collapsedPanels.display && (
          <div className="panel-body">
            <label className="checkbox-row highlight">
              <input
                type="checkbox"
                checked={filters.sizeByLoc}
                onChange={e => onFiltersChange({ ...filters, sizeByLoc: e.target.checked })}
              />
              Size nodes by lines of code
            </label>

            <div className="divider" />
            <h5>Show Node Types</h5>
            <label className="checkbox-row">
              <input
                type="checkbox"
                checked={filters.showModules}
                onChange={e => onFiltersChange({ ...filters, showModules: e.target.checked })}
              />
              Modules / Packages
            </label>
            <label className="checkbox-row">
              <input
                type="checkbox"
                checked={filters.showClasses}
                onChange={e => onFiltersChange({ ...filters, showClasses: e.target.checked })}
              />
              Classes
            </label>
            <label className="checkbox-row">
              <input
                type="checkbox"
                checked={filters.showFunctions}
                onChange={e => onFiltersChange({ ...filters, showFunctions: e.target.checked })}
              />
              Functions / Methods
            </label>
            <label className="checkbox-row">
              <input
                type="checkbox"
                checked={filters.showExternal}
                onChange={e => onFiltersChange({ ...filters, showExternal: e.target.checked })}
              />
              External Dependencies
            </label>
          </div>
        )}
      </div>

      {/* 4. Bottom-Right Massive Objects Drawer */}
      <div className={`hud-panel panel-massive ${collapsedPanels.massive ? 'collapsed' : ''}`}>
        <div className="panel-header" onClick={() => togglePanel('massive')}>
          <h4>🔥 Massive Objects <span className="badge">{massiveObjects.length}</span></h4>
          <button className="toggle-btn">{collapsedPanels.massive ? '+' : '−'}</button>
        </div>
        {!collapsedPanels.massive && (
          <div className="panel-body">
            <div className="threshold-controls">
              <span>Min lines:</span>
              <input
                type="number"
                min="10"
                max="1000"
                value={locThreshold}
                onChange={e => setLocThreshold(parseInt(e.target.value) || 10)}
                className="num-input"
              />
            </div>
            <ul className="item-list">
              {massiveObjects.slice(0, 30).map(item => (
                <li key={item.id} onClick={() => onSelectSymbol(item.id)}>
                  <span className="item-name">{item.label}</span>
                  <span className="item-loc">{item.lines_of_code} LOC</span>
                </li>
              ))}
              {massiveObjects.length === 0 && <li className="empty-msg">No objects exceed {locThreshold} LOC</li>}
            </ul>
          </div>
        )}
      </div>

      {/* 5. Unlinked & Reachability / Risks Drawer */}
      <div className={`hud-panel panel-unlinked ${collapsedPanels.unlinked ? 'collapsed' : ''}`}>
        <div className="panel-header" onClick={() => togglePanel('unlinked')}>
          <h4>🕸️ Unlinked & Risks</h4>
          <button className="toggle-btn">{collapsedPanels.unlinked ? '+' : '−'}</button>
        </div>
        {!collapsedPanels.unlinked && (
          <div className="panel-body">
            <div className="tab-buttons">
              <button className={unlinkedTab === 'unlinked' ? 'active' : ''} onClick={() => setUnlinkedTab('unlinked')}>
                Unlinked ({unlinkedNodes.length})
              </button>
              <button className={unlinkedTab === 'dead' ? 'active' : ''} onClick={() => setUnlinkedTab('dead')}>
                Dead Code ({deadCodeNodes.length})
              </button>
              <button className={unlinkedTab === 'risks' ? 'active' : ''} onClick={() => setUnlinkedTab('risks')}>
                Risks ({riskNodes.length})
              </button>
            </div>

            <ul className="item-list">
              {unlinkedTab === 'unlinked' && unlinkedNodes.slice(0, 30).map(item => (
                <li key={item.id} onClick={() => onSelectSymbol(item.id)}>
                  <span className="item-name">{item.id}</span>
                </li>
              ))}
              {unlinkedTab === 'dead' && deadCodeNodes.slice(0, 30).map(item => (
                <li key={item.id} onClick={() => onSelectSymbol(item.id)}>
                  <span className={`verdict-badge verdict-${item.verdict}`}>{item.verdict}</span>
                  <span className="item-name">{item.label}</span>
                </li>
              ))}
              {unlinkedTab === 'risks' && riskNodes.slice(0, 30).map(item => (
                <li key={item.id} onClick={() => onSelectSymbol(item.id)}>
                  <span className="risk-badge">RISK</span>
                  <span className="item-name">{item.label}</span>
                </li>
              ))}
            </ul>
          </div>
        )}
      </div>
    </div>
  )
}
