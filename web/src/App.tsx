import { BrowserRouter, Routes, Route } from 'react-router-dom'
import TopNav from './components/TopNav'
import RankingsPage from './pages/RankingsPage'
import EventsPage from './pages/EventsPage'
import MatchupsPage from './pages/MatchupsPage'
import FighterProfilePage from './pages/FighterProfilePage'

export default function App() {
  return (
    <BrowserRouter>
      <TopNav />
      <Routes>
        <Route path="/" element={<RankingsPage />} />
        <Route path="/events" element={<EventsPage />} />
        <Route path="/matchups" element={<MatchupsPage />} />
        <Route path="/fighter/:id" element={<FighterProfilePage />} />
      </Routes>
    </BrowserRouter>
  )
}
