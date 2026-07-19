// Display helpers. Missing values render as an em dash, never NaN/undefined.

export const DASH = '—'

export function fmtNum(v: number | null | undefined, digits = 0): string {
  if (v === null || v === undefined || Number.isNaN(v)) return DASH
  return v.toLocaleString('en-US', {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  })
}

export function fmtRating(mu: number | null | undefined): string {
  if (mu === null || mu === undefined || Number.isNaN(mu)) return DASH
  return Math.round(mu).toString()
}

export function fmtRatingWithRd(
  mu: number | null | undefined,
  rd: number | null | undefined,
): string {
  if (mu === null || mu === undefined || Number.isNaN(mu)) return DASH
  if (rd === null || rd === undefined || Number.isNaN(rd)) return fmtRating(mu)
  return `${Math.round(mu)} ± ${Math.round(rd)}`
}

export function fmtPct(frac: number | null | undefined): string {
  if (frac === null || frac === undefined || Number.isNaN(frac)) return DASH
  return `${Math.round(frac * 100)}%`
}

export function fmtDate(s: string | null | undefined): string {
  if (!s) return DASH
  // Parse date-only strings (YYYY-MM-DD) as local, not UTC, to avoid a
  // timezone shift that can display the previous day.
  const dateOnly = /^\d{4}-\d{2}-\d{2}$/.exec(s)
  const d = dateOnly
    ? new Date(Number(s.slice(0, 4)), Number(s.slice(5, 7)) - 1, Number(s.slice(8, 10)))
    : new Date(s)
  if (Number.isNaN(d.getTime())) return s
  return d.toLocaleDateString('en-US', {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
  })
}

export function fmtHeight(inches: number | null | undefined): string {
  if (inches === null || inches === undefined || Number.isNaN(inches))
    return DASH
  const ft = Math.floor(inches / 12)
  const inch = Math.round(inches - ft * 12)
  return `${ft}'${inch}"`
}

export function fmtInches(v: number | null | undefined): string {
  if (v === null || v === undefined || Number.isNaN(v)) return DASH
  return `${v}"`
}

export function ageFromDob(dob: string | null | undefined): string {
  if (!dob) return DASH
  const d = new Date(dob)
  if (Number.isNaN(d.getTime())) return DASH
  const now = new Date()
  let age = now.getFullYear() - d.getFullYear()
  const m = now.getMonth() - d.getMonth()
  if (m < 0 || (m === 0 && now.getDate() < d.getDate())) age--
  return age.toString()
}

export function fmtControl(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined || Number.isNaN(seconds))
    return DASH
  const m = Math.floor(seconds / 60)
  const s = Math.round(seconds - m * 60)
  return `${m}:${s.toString().padStart(2, '0')}`
}

export function resultClass(result: string): string {
  const r = result.toLowerCase()
  if (r === 'win') return 'win'
  if (r === 'loss') return 'loss'
  if (r === 'draw') return 'draw'
  return 'nc'
}

export function resultLabel(result: string): string {
  return result.toLowerCase()
}
