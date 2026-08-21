import React from 'react'

interface ToolbarProps {
  onRefresh?: () => void
}

export function Toolbar({ onRefresh }: ToolbarProps) {
  return (
    <div className="toolbar">
      <button onClick={onRefresh} title="Refresh graph">
        🔄 Refresh
      </button>
      <button title="Zoom to fit">
        ⛶ Fit
      </button>
      <button title="Layout">
        ⊟ Layout
      </button>
      <button title="Help">
        ? Help
      </button>
    </div>
  )
}
