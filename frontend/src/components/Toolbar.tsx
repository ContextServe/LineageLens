import { useState, useEffect } from 'react'
import './Toolbar.css'

export type VerdictFilter = 'all' | 'dead' | 'probably_dead' | 'test_only'

interface ToolbarProps {
  onRefresh?: () => void
  testFilter?: 'all' | 'source' | 'tests'
  onTestFilterChange?: (filter: 'all' | 'source' | 'tests') => void
  verdictFilter?: VerdictFilter
  onVerdictFilterChange?: (filter: VerdictFilter) => void
  legendFilters: Record<string, boolean>
  onToggleLegendFilter: (key: string) => void
  nodes?: Array<{ id: string; label: string; kind: string; lines_of_code?: number }>
  onSelectSymbol: (id: string) => void
  selectedSymbol: string | null
  onClearSelection: () => void
}

export function Toolbar({
  onRefresh,
  testFilter = 'all',
  onTestFilterChange,
  verdictFilter = 'all',
  onVerdictFilterChange,
  legendFilters,
  onToggleLegendFilter,
  nodes = [],
  onSelectSymbol,
  selectedSymbol,
  onClearSelection,
}: ToolbarProps) {
  const [searchQuery, setSearchQuery] = useState('')
  const [searchOpen, setSearchOpen] = useState(false)

  // Keyboard shortcut Ctrl+F or Cmd+F for search focus
  useEffect(() => {
    function handleKeyDown(e: KeyboardEvent) {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'f') {
        e.preventDefault()
        const input = document.getElementById('toolbarSearchInput')
        if (input) input.focus()
      } else if (e.key === 'Escape') {
        onClearSelection()
        setSearchQuery('')
        setSearchOpen(false)
      }
    }
    window.addEventListener('keydown', handleKeyDown)
    return () => window.removeEventListener('keydown', handleKeyDown)
  }, [onClearSelection])

  const searchResults = searchQuery.trim().length >= 2
    ? nodes.filter(n => n.id.toLowerCase().includes(searchQuery.toLowerCase()) || n.label.toLowerCase().includes(searchQuery.toLowerCase())).slice(0, 15)
    : []

  return (
    <div className="toolbar-container">
      <div className="toolbar">
        {/* Actions Group */}
        <div className="toolbar-group">
          <button onClick={onRefresh} title="Refresh graph">
            🔄 Refresh
          </button>
          <button onClick={() => window.dispatchEvent(new CustomEvent('cy-fit'))} title="Zoom to fit">
            ⛶ Fit
          </button>
        </div>

        {/* Integrated Search Input */}
        <div className="toolbar-search-wrapper">
          <input
            id="toolbarSearchInput"
            type="text"
            className="toolbar-search-input"
            placeholder="Search symbols/modules... (Ctrl+F)"
            value={searchQuery}
            onChange={e => {
              setSearchQuery(e.target.value)
              setSearchOpen(true)
            }}
            onFocus={() => setSearchOpen(true)}
          />
          {searchQuery && (
            <button className="search-clear-btn" onClick={() => setSearchQuery('')}>×</button>
          )}

          {searchOpen && searchResults.length > 0 && (
            <ul className="toolbar-search-dropdown">
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

        {/* Selected Focus Badge */}
        {selectedSymbol && (
          <div className="selected-target-badge">
            <span>Target: <strong>{selectedSymbol.split('::').pop()?.split('.').pop()}</strong></span>
            <button onClick={onClearSelection} className="btn-clear-target">Clear Focus (Esc)</button>
          </div>
        )}

        {/* Segmented Control */}
        <div className="toolbar-group">
          <div className="segmented-control">
            <button
              className={testFilter === 'all' ? 'active' : ''}
              onClick={() => onTestFilterChange?.('all')}
              title="Show all source and test files"
            >
              All files
            </button>
            <button
              className={testFilter === 'source' ? 'active' : ''}
              onClick={() => onTestFilterChange?.('source')}
              title="Show source files only"
            >
              Source only
            </button>
            <button
              className={testFilter === 'tests' ? 'active' : ''}
              onClick={() => onTestFilterChange?.('tests')}
              title="Show test files only"
            >
              Tests only
            </button>
          </div>
        </div>

        {/* Verdict Select */}
        <div className="toolbar-group">
          <select
            className="verdict-filter"
            value={verdictFilter}
            onChange={(e) => onVerdictFilterChange?.(e.target.value as VerdictFilter)}
            title="Filter by reachability verdict"
          >
            <option value="all">All verdicts</option>
            <option value="dead">Dead only</option>
            <option value="probably_dead">Probably dead only</option>
            <option value="test_only">Reached only by tests</option>
          </select>
        </div>
      </div>

      {/* Interactive Filter Legend Row */}
      <div className="toolbar-legend-bar">
        <span className="legend-label">Interactive Filters:</span>
        <button
          className={`legend-btn ${legendFilters.entry_point !== false ? 'active' : 'muted'}`}
          onClick={() => onToggleLegendFilter('entry_point')}
          title="Click to toggle Entry Points visibility"
        >
          <span className="color-box entry-point"></span>
          Entry Point
        </button>

        <button
          className={`legend-btn ${legendFilters.risk !== false ? 'active' : 'muted'}`}
          onClick={() => onToggleLegendFilter('risk')}
          title="Click to toggle Resiliency Risks visibility"
        >
          <span className="color-box risk"></span>
          Risk
        </button>

        <button
          className={`legend-btn ${legendFilters.dead !== false ? 'active' : 'muted'}`}
          onClick={() => onToggleLegendFilter('dead')}
          title="Click to toggle Dead Code visibility"
        >
          <span className="color-box dead-code"></span>
          Dead
        </button>

        <button
          className={`legend-btn ${legendFilters.probably_dead !== false ? 'active' : 'muted'}`}
          onClick={() => onToggleLegendFilter('probably_dead')}
          title="Click to toggle Probably Dead visibility"
        >
          <span className="color-box probably-dead"></span>
          Probably dead
        </button>

        <button
          className={`legend-btn ${legendFilters.test_only !== false ? 'active' : 'muted'}`}
          onClick={() => onToggleLegendFilter('test_only')}
          title="Click to toggle Test Only visibility"
        >
          <span className="color-box test-only"></span>
          Test only
        </button>

        <button
          className={`legend-btn ${legendFilters.dynamic_only !== false ? 'active' : 'muted'}`}
          onClick={() => onToggleLegendFilter('dynamic_only')}
          title="Click to toggle Dynamic Only visibility"
        >
          <span className="color-box dynamic-only"></span>
          Dynamic only
        </button>

        <button
          className={`legend-btn ${legendFilters.duplicate !== false ? 'active' : 'muted'}`}
          onClick={() => onToggleLegendFilter('duplicate')}
          title="Click to toggle Duplicate Names visibility"
        >
          <span className="color-box duplicate"></span>
          Duplicate Name
        </button>
      </div>
    </div>
  )
}
