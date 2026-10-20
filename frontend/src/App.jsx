import { useState } from 'react'
import { APP_NAME, ORGANIZATIONS } from './config'
import Dashboard from './Dashboard'

export default function App() {
  const [org, setOrg] = useState(ORGANIZATIONS[0].slug)

  return (
    <div className="app">
      <header className="topbar">
        <h1>{APP_NAME}</h1>
        <p className="tagline">Bank reconciliation for capital calls</p>
        <label className="org-switcher">
          Organization
          <select value={org} onChange={(event) => setOrg(event.target.value)}>
            {ORGANIZATIONS.map((o) => (
              <option key={o.slug} value={o.slug}>
                {o.name}
              </option>
            ))}
          </select>
        </label>
      </header>
      <main>
        {/* A new key remounts the dashboard, so nothing from the previous org survives. */}
        <Dashboard key={org} org={org} />
      </main>
    </div>
  )
}