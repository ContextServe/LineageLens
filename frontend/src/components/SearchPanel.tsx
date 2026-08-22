import React, { useState } from 'react'

interface SearchPanelProps {
  onSelectSymbol?: (id: string) => void
}

export function SearchPanel({ onSelectSymbol }: SearchPanelProps) {
  const [query, setQuery] = useState('')
  const [results, setResults] = useState<any[]>([])

  async function handleSearch(text: string) {
    setQuery(text)
    if (text.length < 2) {
      setResults([])
      return
    }

    try {
      const response = await fetch(`/api/v1/search?text=${encodeURIComponent(text)}&limit=15`)
      if (response.ok) {
        setResults(await response.json())
      }
    } catch (err) {
      console.error('Search failed:', err)
    }
  }

  return (
    <div className="search-panel">
      <h2>Search</h2>
      <input
        type="text"
        placeholder="Find a symbol..."
        value={query}
        onChange={(e) => handleSearch(e.target.value)}
      />
      <div className="results">
        {results.map((sym) => (
          <button
            key={sym.id}
            className="result-item"
            onClick={() => onSelectSymbol?.(sym.id)}
          >
            <div className="name">{sym.name}</div>
            <div className="kind">{sym.kind}</div>
          </button>
        ))}
      </div>
    </div>
  )
}
