import React from 'react'
import './Toolbar.css'

interface ToolbarProps {
  onRefresh?: () => void
  testFilter?: 'all' | 'source' | 'tests'
  onTestFilterChange?: (filter: 'all' | 'source' | 'tests') => void
}

export function Toolbar({ onRefresh, testFilter = 'all', onTestFilterChange }: ToolbarProps) {
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
          Possibly Dead
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
