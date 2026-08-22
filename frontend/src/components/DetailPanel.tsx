import React, { useState, useEffect } from 'react'

interface DetailPanelProps {
  symbolId: string
}

export function DetailPanel({ symbolId }: DetailPanelProps) {
  const [symbol, setSymbol] = useState<any>(null)
  const [impact, setImpact] = useState<any>(null)

  useEffect(() => {
    if (!symbolId) return

    // Fetch symbol details
    fetch(`/api/v1/symbols/${encodeURIComponent(symbolId)}`)
      .then((r) => r.json())
      .then(setSymbol)
      .catch(console.error)

    // Fetch impact analysis
    fetch(`/api/v1/symbols/${encodeURIComponent(symbolId)}/impact`)
      .then((r) => r.json())
      .then(setImpact)
      .catch(console.error)
  }, [symbolId])

  if (!symbol) return <div className="detail-panel loading">Loading...</div>

  return (
    <div className="detail-panel">
      <h2>{symbol.name}</h2>
      <div className="meta">
        <span className="kind">{symbol.kind}</span>
        {symbol.entry_point && <span className="entry-point">{symbol.entry_point}</span>}
        {symbol.async_ && <span className="async">async</span>}
      </div>

      {symbol.description && (
        <div className="section">
          <h3>Description</h3>
          <p>{symbol.description}</p>
        </div>
      )}

      {symbol.inputs?.length > 0 && (
        <div className="section">
          <h3>Inputs</h3>
          <ul>
            {symbol.inputs.map((inp: any, i: number) => (
              <li key={i}>
                <code>{inp.name}</code>: {inp.type}
              </li>
            ))}
          </ul>
        </div>
      )}

      {symbol.outputs?.length > 0 && (
        <div className="section">
          <h3>Outputs</h3>
          <ul>
            {symbol.outputs.map((out: any, i: number) => (
              <li key={i}>{out.type}</li>
            ))}
          </ul>
        </div>
      )}

      {impact && (
        <div className="section">
          <h3>Impact</h3>
          <p>{impact.affected.length} symbols affected if changed</p>
          {impact.affected_entry_points?.length > 0 && (
            <div>
              <strong>Entry points affected:</strong>
              <ul>
                {impact.affected_entry_points.slice(0, 5).map((ep: string, i: number) => (
                  <li key={i}>{ep}</li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}

      <div className="code-location">
        <a href={`#${symbol.file}:${symbol.line}`} target="_blank" rel="noreferrer">
          {symbol.file}:{symbol.line}
        </a>
      </div>
    </div>
  )
}
