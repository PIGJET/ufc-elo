import { NavLink } from 'react-router-dom'

export default function TopNav() {
  return (
    <nav className="topnav">
      <div className="topnav-inner">
        <div className="topnav-links">
          <NavLink to="/" className="topnav-link" end>
            Rankings
          </NavLink>
          <NavLink to="/events" className="topnav-link">
            Events
          </NavLink>
        </div>

        <NavLink to="/" className="wordmark" aria-label="UFC Elo home">
          <span className="wordmark-text">UFC ELO</span>
          <span className="wordmark-bar" />
        </NavLink>

        <div className="topnav-links">
          <NavLink to="/matchups" className="topnav-link">
            Matchups
          </NavLink>
        </div>
      </div>
    </nav>
  )
}
