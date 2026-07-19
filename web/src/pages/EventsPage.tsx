import { useEffect, useState } from 'react'
import type { EventsResponse, EventFight, UpcomingEvent } from '../api/types'
import { getUpcomingEvents } from '../api/client'
import { fmtDate, fmtRating } from '../api/format'
import MatchupModule from '../components/MatchupModule'

function Chevron({ open }: { open: boolean }) {
  return (
    <svg
      className={`chevron${open ? ' open' : ''}`}
      viewBox="0 0 24 24"
      width="20"
      height="20"
      aria-hidden="true"
    >
      <path
        d="M6 9l6 6 6-6"
        fill="none"
        stroke="currentColor"
        strokeWidth="2.5"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  )
}

function fightTag(fight: EventFight): string {
  let tag = fight.weight_class ?? 'Bout'
  if (fight.is_interim_title) tag = `Interim ${tag} Title`
  else if (fight.is_title) tag = `${tag} Title`
  return tag
}

// Level 2: a single fight. Collapsed = a compact row; expanded mounts the
// (heavy) comparison module. The module is only rendered while open.
function FightRow({ fight }: { fight: EventFight }) {
  const [open, setOpen] = useState(false)
  return (
    <div className={`fight-acc${open ? ' open' : ''}`}>
      <button
        type="button"
        className="fight-acc-head"
        aria-expanded={open}
        onClick={() => setOpen((o) => !o)}
      >
        <span className="fight-acc-corners">
          <span className="fac-name red">{fight.red.name ?? 'TBD'}</span>
          <span className="fac-vs">vs</span>
          <span className="fac-name blue">{fight.blue.name ?? 'TBD'}</span>
        </span>
        <span className="fight-acc-meta">
          <span className="fac-rating">
            {fmtRating(fight.red.mu)} · {fmtRating(fight.blue.mu)}
          </span>
          {fight.is_main_event && <span className="fac-tag main">Main Event</span>}
          <span className={`fac-tag${fight.is_title || fight.is_interim_title ? ' title' : ''}`}>
            {fightTag(fight)}
          </span>
          <Chevron open={open} />
        </span>
      </button>
      {open && (
        <div className="fight-acc-body">
          <MatchupModule
            red={fight.red}
            blue={fight.blue}
            redStats={fight.stats.red}
            blueStats={fight.stats.blue}
            prediction={fight.prediction}
            ratingPreview={fight.rating_preview}
            odds={fight.odds}
          />
        </div>
      )}
    </div>
  )
}

// Level 1: a single event. Collapsed by default; expanding reveals its fights.
function EventAccordion({ ev }: { ev: UpcomingEvent }) {
  const [open, setOpen] = useState(false)
  const loc = [ev.venue, ev.location].filter(Boolean).join(' · ')
  return (
    <div className={`event-acc${open ? ' open' : ''}`}>
      <button
        type="button"
        className="event-acc-head"
        aria-expanded={open}
        onClick={() => setOpen((o) => !o)}
      >
        <span className="event-acc-title">
          <span className="event-name">{ev.name}</span>
          {loc && <span className="event-loc">{loc}</span>}
        </span>
        <span className="event-acc-right">
          <span className="event-date">{fmtDate(ev.date)}</span>
          <Chevron open={open} />
        </span>
      </button>
      {open && (
        <div className="event-acc-body">
          {ev.fights.length === 0 ? (
            <div className="empty-note">No fights announced yet.</div>
          ) : (
            ev.fights.map((f) => <FightRow fight={f} key={f.fight_id} />)
          )}
        </div>
      )}
    </div>
  )
}

export default function EventsPage() {
  const [data, setData] = useState<EventsResponse | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    getUpcomingEvents()
      .then(setData)
      .catch((e) => setError(String(e)))
  }, [])

  if (error) return <div className="error-box">Failed to load events: {error}</div>
  if (!data) return <div className="loading">Loading events…</div>

  return (
    <div className="page">
      <h1 className="page-title">
        Upcoming <span className="accent">Events</span>
      </h1>
      {data.events.length === 0 && (
        <div className="empty-note">No upcoming events.</div>
      )}
      {data.events.map((ev) => (
        <EventAccordion ev={ev} key={ev.id} />
      ))}
    </div>
  )
}
