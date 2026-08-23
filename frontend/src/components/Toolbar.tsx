import React from 'react'
import './Toolbar.css'

export type VerdictFilter = 'all' | 'dead' | 'probably_dead' | 'test_only'

interface ToolbarProps {
  onRefresh?: () => void
  testFilter?: 'all' | 'source' | 'tests'
  onTestFilterChange?: (filter: 'all' | 'source' | 'tests') => void
  verdictFilter?: VerdictFilter
  onVerdictFilterChange?: (filter: VerdictFilter) => void
}

export function Toolbar({
  onRefresh,
  testFilter = 'all',
  onTestFilterChange,
  verdictFilter = 'all',
  onVerdictFilterChange,
}: ToolbarProps) {
  return (
    <div className="toolbar">
      <div className="toolbar-group">
        <button onClick={onRefresh} title="Refresh graph">
          🔄 Refresh
        </button>
        <button title="Zoom to fit">
          ⛶ Fit
        </button>
        <button title="Layout">
          ⊟ Layout
        </button>
      </div>

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

      <div className="toolbar-legend">
        <div className="legend-item">
          <span className="color-box entry-point"></span>
          Entry Point
        </div>
        <div className="legend-item">
          <span className="color-box risk"></span>
          Risk
        </div>
        <div className="legend-item">
          <span className="color-box dead-code"></span>
          Dead
        </div>
        <div className="legend-item">
          <span className="color-box probably-dead"></span>
          Probably dead
        </div>
        <div className="legend-item">
          <span className="color-box test-only"></span>
          Test only
        </div>
        <div className="legend-item">
          <span className="color-box dynamic-only"></span>
          Dynamic only
        </div>
        <div className="legend-item">
          <span className="color-box duplicate"></span>
          Duplicate Name
        </div>
      </div>

      <button title="Help">
        ? Help
      </button>
    </div>
  )
}
