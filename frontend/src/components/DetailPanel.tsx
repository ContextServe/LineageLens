import { useState, useEffect } from 'react'

interface DetailPanelProps {
  symbolId: string
}

/**
 * Every query route returns the engine's own envelope:
 *
 *   { kind, returned, total_available, truncated, results: [...], coverage }
 *
 * The payload is one level down, under `results`, and `coverage` states what
 * the answer does not cover. Reading `results[0]` rather than the body itself
 * is the shape change from the schema-3 API, which returned flat objects and
 * had nowhere to put the envelope.
 */
async function queryOne(url: string): Promise<any | null> {
  const response = await fetch(url)
  if (!response.ok) return null
  const body = await response.json()
  return body?.results?.[0] ?? null
}

async function queryAll(url: string): Promise<any | null> {
  const response = await fetch(url)
  if (!response.ok) return null
  return await response.json()
}

export function DetailPanel({ symbolId }: DetailPanelProps) {
  const [symbol, setSymbol] = useState<any>(null)
  const [impact, setImpact] = useState<any>(null)
  const [callers, setCallers] = useState<any>(null)

  useEffect(() => {
    if (!symbolId) return

    setSymbol(null)
    setImpact(null)
    setCallers(null)

    // `symbolId` comes from the graph view, where node ids are content
    // hashes with no URL-special characters. Encoded anyway: the panel can
    // also be opened with a qualified name, which contains `#` — and an
    // unencoded `#` is a fragment the server never receives, so it would
    // silently answer about the enclosing module instead (#67).
    const id = encodeURIComponent(symbolId)

    queryOne(`/api/v1/symbols/${id}?intent=precise`).then(setSymbol).catch(console.error)
    queryOne(`/api/v1/symbols/${id}/impact`).then(setImpact).catch(console.error)
    queryAll(`/api/v1/symbols/${id}/callers`).then(setCallers).catch(console.error)

    // Reachability verdicts have no schema-4 producer and the route answers
    // 501 (tracked in #49). Deliberately not fetched: showing "no verdict"
    // would be indistinguishable from a verdict of "reachable".
  }, [symbolId])

  if (!symbol) return <div className="detail-panel loading">Loading...</div>

  const [location] = String(symbol.at ?? '').split('-')
  const flags: string[] = symbol.flags ?? []

  return (
    <div className="detail-panel">
      <h2>{symbol.node}</h2>
      <div className="meta">
        <span className="kind">{symbol.kind}</span>
        {symbol.lang && <span className="lang">{symbol.lang}</span>}
        {symbol.service && <span className="service">{symbol.service}</span>}
        {flags.map((flag) => (
          <span key={flag} className={`flag flag-${flag.toLowerCase()}`}>
            {flag.toLowerCase().replace('_', ' ')}
          </span>
        ))}
      </div>

      {symbol.signature && (
        <div className="section">
          <h3>Signature</h3>
          <pre>
            <code>{symbol.signature}</code>
          </pre>
        </div>
      )}

      {symbol.docstring && (
        <div className="section">
          <h3>Description</h3>
          <p>{symbol.docstring}</p>
        </div>
      )}

      {impact && (
        <div className="section impact">
          <h3>Blast radius</h3>
          <p>
            {impact.change_kind && <span className="change-kind">{impact.change_kind}</span>}
            {impact.in_process?.length ?? 0} dependents in process
          </p>
          {impact.entry_points?.length > 0 ? (
            <div>
              <strong>Reaches these entry points:</strong>
              <ul>
                {impact.entry_points.slice(0, 5).map((ep: string, i: number) => (
                  <li key={i}>{ep}</li>
                ))}
              </ul>
            </div>
          ) : (
            // `entry_points` is absent whenever it is empty, and it is
            // currently always empty: NodeFlags.ENTRY_POINT is never set
            // (#57). Until that lands, absence is not evidence that a change
            // stays internal, and saying so is better than silence.
            <p className="caveat">
              Entry-point reachability not yet computed (#57) — absence here
              does not mean this change is internal.
            </p>
          )}
          {impact.cross_service?.length > 0 && (
            <div>
              <strong>Crosses a service boundary:</strong>
              <ul>
                {impact.cross_service.slice(0, 5).map((c: any, i: number) => (
                  <li key={i}>{c.contract ?? JSON.stringify(c)}</li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}

      {callers && (
        <div className="section">
          <h3>Callers</h3>
          <p>
            {callers.returned} of {callers.total_available}
            {callers.truncated && ' (truncated)'}
          </p>
          {callers.coverage && !callers.coverage.complete && (
            <p className="caveat">
              Incomplete: {Object.keys(callers.coverage.boundaries ?? {}).join(', ') ||
                'unparsed files in scope'}
            </p>
          )}
        </div>
      )}

      {location && (
        <div className="code-location">
          <a href={`#${location}`} target="_blank" rel="noreferrer">
            {location}
          </a>
        </div>
      )}
    </div>
  )
}
