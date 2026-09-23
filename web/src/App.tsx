import { lazy, Suspense } from 'react'
import { BrowserRouter, Routes, Route } from 'react-router-dom'
import TopNav from './components/TopNav'

const RankingsPage = lazy(() => import('./pages/RankingsPage'))
const EventsPage = lazy(() => import('./pages/EventsPage'))
const MatchupsPage = lazy(() => import('./pages/MatchupsPage'))
const FighterProfilePage = lazy(() => import('./pages/FighterProfilePage'))

export default function App() {
  return (
    <BrowserRouter>
      <TopNav />
      <Suspense fallback={<div className="loading">Loading…</div>}>
        <Routes>
          <Route path="/" element={<RankingsPage />} />
          <Route path="/events" element={<EventsPage />} />
          <Route path="/matchups" element={<MatchupsPage />} />
          <Route path="/fighter/:id" element={<FighterProfilePage />} />
        </Routes>
      </Suspense>
    </BrowserRouter>
  )
}
