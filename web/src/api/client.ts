import type {
  RankingsResponse,
  SearchResponse,
  FighterProfile,
  EventsResponse,
  MatchupResponse,
} from './types'

async function getJSON<T>(url: string): Promise<T> {
  const res = await fetch(url)
  if (!res.ok) {
    throw new Error(`Request failed (${res.status}) for ${url}`)
  }
  return (await res.json()) as T
}

export function getRankings(): Promise<RankingsResponse> {
  return getJSON<RankingsResponse>('/api/rankings')
}

export function getRankingsElo(): Promise<RankingsResponse> {
  return getJSON<RankingsResponse>('/api/rankings/elo')
}

export function searchFighters(query: string): Promise<SearchResponse> {
  return getJSON<SearchResponse>(
    `/api/fighters?search=${encodeURIComponent(query)}`,
  )
}

export function getFighter(id: number | string): Promise<FighterProfile> {
  return getJSON<FighterProfile>(`/api/fighters/${id}`)
}

export function getUpcomingEvents(): Promise<EventsResponse> {
  return getJSON<EventsResponse>('/api/events/upcoming')
}

export function getMatchup(
  a: number | string,
  b: number | string,
): Promise<MatchupResponse> {
  return getJSON<MatchupResponse>(`/api/matchup?a=${a}&b=${b}`)
}
