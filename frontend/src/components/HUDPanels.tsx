import { useState } from 'react'
import type { GraphNode } from '../types'

export interface ScopeFilters {
  showModules: boolean
  showClasses: boolean
  showFunctions: boolean
  showExternal: boolean
  sizeByLoc: boolean
  linkKinds: Record<string, boolean>
}

// One definition, in `types.ts`. This alias stays so existing imports of
// `NodeItem` keep working; the fields live with the API shape they mirror.
export type NodeItem = GraphNode

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
  // Accepted and unused. `DetailPanel` owns the selected symbol, so these are
  // vestiges of an earlier split where the HUD did. Kept in the signature so
  // `App.tsx` does not change shape, underscored so the compiler stops
  // reporting them and a reader knows it is deliberate rather than a bug.
  selectedSymbol: _selectedSymbol,
  onClearSelection: _onClearSelection,
}: HUDPanelsProps) {
  // Collapsible states
  const [collapsedPanels, setCollapsedPanels] = useState<Record<string, boolean>>({
    stats: false,
    massive: true,
    unlinked: true,
    display: false,
  })

  // Massive objects state
  const [locThreshold, setLocThreshold] = useState(50)
  // Never updated: the kind filter is fixed for now, and a setter nothing
  // calls is what a UI control was meant to use.
  const [massiveKinds] = useState({
    module: true,
    class: true,
    function: true,
    method: true,
  })

  // Unlinked tab state
  const [unlinkedTab, setUnlinkedTab] = useState<'unlinked' | 'dead' | 'risks'>('unlinked')

  const togglePanel = (panelKey: string) => {
    setCollapsedPanels(prev => ({ ...prev, [panelKey]: !prev[panelKey] }))
  }

  // Calculate statistics
  const totalModules = nodes.filter(n => n.kind === 'module' || n.kind === 'package').length
  const totalClasses = nodes.filter(n => n.kind === 'class').length
  const totalFunctions = nodes.filter(n => n.kind === 'function' || n.kind === 'method').length
  // Both of these are null on every node until #60 and #49 land. Counting
  // them would render a confident `0`, which reads as "analysed, none found"
  // -- the one thing an unanalysed field must never look like. `null` here is
  // rendered as an em dash instead (#64).
  const risksComputed = nodes.some(n => n.has_resiliency_flag !== null)
  const verdictsComputed = nodes.some(n => n.verdict !== null)
  const totalRisks = risksComputed
    ? nodes.filter(n => n.has_resiliency_flag).length
    : null
  const deadCodeCount = verdictsComputed
    ? nodes.filter(n => n.verdict === 'dead' || n.verdict === 'probably_dead').length
    : null

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
      {/* Top-Right Column (Statistics & Unlinked/Risks) */}
      <div className="hud-column top-right">
        {/* Statistics Panel */}
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
              <div className={`stat-item ${deadCodeCount === null ? 'unsupported' : 'danger'}`}
                   title={deadCodeCount === null ? 'Not computed yet (#49)' : undefined}>
                <span className="stat-num">{deadCodeCount ?? '\u2014'}</span>
                <span className="stat-lbl">Dead Code</span>
              </div>
              <div className={`stat-item ${totalRisks === null ? 'unsupported' : 'warning'}`}
                   title={totalRisks === null ? 'Not computed yet (#60)' : undefined}>
                <span className="stat-num">{totalRisks ?? '\u2014'}</span>
                <span className="stat-lbl">Risks</span>
              </div>
            </div>
          )}
        </div>

        {/* Unlinked & Reachability / Risks Panel */}
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
                    <span className="item-name">{item.label || item.id}</span>
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

      {/* Bottom-Left Column (Display & Layout Controls) */}
      <div className="hud-column bottom-left">
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
      </div>

      {/* Bottom-Right Column (Massive Objects Drawer) */}
      <div className="hud-column bottom-right">
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
      </div>
    </div>
  )
}
