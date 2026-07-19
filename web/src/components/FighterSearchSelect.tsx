import { useEffect, useRef, useState } from 'react'
import type { SearchResult } from '../api/types'
import { searchFighters } from '../api/client'
import { fmtRating } from '../api/format'
import FighterImage from './FighterImage'

interface Props {
  label: string
  corner: 'red' | 'blue'
  selected: SearchResult | null
  onSelect: (f: SearchResult | null) => void
}

/** Bold the first case-insensitive occurrence of the query inside a name. */
function Highlight({ text, query }: { text: string; query: string }) {
  const q = query.trim()
  const i = q ? text.toLowerCase().indexOf(q.toLowerCase()) : -1
  if (i < 0) return <>{text}</>
  return (
    <>
      {text.slice(0, i)}
      <strong>{text.slice(i, i + q.length)}</strong>
      {text.slice(i + q.length)}
    </>
  )
}

export default function FighterSearchSelect({
  label,
  corner,
  selected,
  onSelect,
}: Props) {
  const [query, setQuery] = useState('')
  const [results, setResults] = useState<SearchResult[]>([])
  const [open, setOpen] = useState(false)
  const [loading, setLoading] = useState(false)
  const [active, setActive] = useState(0)
  const boxRef = useRef<HTMLDivElement>(null)
  const listRef = useRef<HTMLDivElement>(null)

  // debounced search
  useEffect(() => {
    const q = query.trim()
    if (q.length < 1 || (selected && q === selected.name)) {
      setResults([])
      setLoading(false)
      return
    }
    setLoading(true)
    const t = setTimeout(() => {
      searchFighters(q)
        .then((r) => {
          setResults(r.results)
          setActive(0)
        })
        .catch(() => setResults([]))
        .finally(() => setLoading(false))
    }, 200)
    return () => clearTimeout(t)
  }, [query, selected])

  // close on outside click
  useEffect(() => {
    function onDoc(e: MouseEvent) {
      if (boxRef.current && !boxRef.current.contains(e.target as Node)) {
        setOpen(false)
      }
    }
    document.addEventListener('mousedown', onDoc)
    return () => document.removeEventListener('mousedown', onDoc)
  }, [])

  // keep the active option scrolled into view
  useEffect(() => {
    listRef.current
      ?.querySelector('.search-result-item.active')
      ?.scrollIntoView({ block: 'nearest' })
  }, [active])

  function pick(f: SearchResult) {
    onSelect(f)
    setQuery(f.name)
    setOpen(false)
  }

  function onKeyDown(e: React.KeyboardEvent<HTMLInputElement>) {
    if (!open || results.length === 0) {
      if (e.key === 'ArrowDown' && results.length > 0) setOpen(true)
      return
    }
    if (e.key === 'ArrowDown') {
      e.preventDefault()
      setActive((a) => (a + 1) % results.length)
    } else if (e.key === 'ArrowUp') {
      e.preventDefault()
      setActive((a) => (a - 1 + results.length) % results.length)
    } else if (e.key === 'Enter') {
      e.preventDefault()
      pick(results[active])
    } else if (e.key === 'Escape') {
      setOpen(false)
    }
  }

  const showList = open && query.trim().length >= 1 && !selected
  const optionId = (i: number) => `${corner}-opt-${i}`

  return (
    <div className="search-select" ref={boxRef}>
      <label className={`picker-label ${corner}`}>{label}</label>
      <input
        className="search-input"
        placeholder="Search a fighter…"
        value={query}
        role="combobox"
        aria-expanded={showList && results.length > 0}
        aria-autocomplete="list"
        aria-controls={`${corner}-listbox`}
        aria-activedescendant={
          showList && results.length > 0 ? optionId(active) : undefined
        }
        onChange={(e) => {
          setQuery(e.target.value)
          setOpen(true)
          if (selected) onSelect(null)
        }}
        onFocus={() => setOpen(true)}
        onKeyDown={onKeyDown}
      />
      {showList && (
        <div className="search-results" id={`${corner}-listbox`} role="listbox" ref={listRef}>
          {results.map((f, i) => (
            <div
              className={`search-result-item${i === active ? ' active' : ''}`}
              key={f.id}
              id={optionId(i)}
              role="option"
              aria-selected={i === active}
              onMouseEnter={() => setActive(i)}
              onClick={() => pick(f)}
            >
              <div className="search-result-face">
                <FighterImage src={f.image_url} alt={f.name} />
              </div>
              <div style={{ flex: 1 }}>
                <div className="search-result-name">
                  <Highlight text={f.name} query={query} />
                </div>
                <div className="search-result-sub">
                  {f.record}
                  {f.division ? ` · ${f.division}` : ''}
                </div>
              </div>
              <div className="mm-corner-rating">{fmtRating(f.mu)}</div>
            </div>
          ))}
          {!loading && results.length === 0 && (
            <div className="search-empty">No fighters found</div>
          )}
          {loading && results.length === 0 && (
            <div className="search-empty">Searching…</div>
          )}
        </div>
      )}
    </div>
  )
}
