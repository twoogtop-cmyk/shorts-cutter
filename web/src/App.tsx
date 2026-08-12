import { NavLink, Navigate, Route, Routes } from 'react-router-dom'
import { HomePage } from './pages/HomePage'
import { ShortsPage } from './pages/ShortsPage'
import { BannersPage } from './pages/BannersPage'
import { TranscriptPage } from './pages/TranscriptPage'
import { SettingsPage } from './pages/SettingsPage'

const NAV = [
  { to: '/home', label: 'Главная' },
  { to: '/shorts', label: 'Шортсы' },
  { to: '/transcript', label: 'Транскрипция' },
  { to: '/banners', label: 'Баннеры' },
  { to: '/settings', label: 'Настройки' },
]

export default function App() {
  return (
    <div className="min-h-full flex flex-col">
      <header className="border-b border-[var(--color-line)] bg-[var(--color-panel)]">
        <div className="mx-auto max-w-[1400px] px-5 h-14 flex items-center gap-6">
          <div className="font-semibold tracking-tight">
            Shorts Cutter
          </div>
          <nav className="flex items-center gap-1">
            {NAV.map((item) => (
              <NavLink
                key={item.to}
                to={item.to}
                className={({ isActive }) =>
                  `px-3 py-1.5 rounded-md text-sm transition-colors ${
                    isActive
                      ? 'bg-[var(--color-panel-2)] text-white'
                      : 'text-neutral-400 hover:text-white hover:bg-[var(--color-panel-2)]/60'
                  }`
                }
              >
                {item.label}
              </NavLink>
            ))}
          </nav>
        </div>
      </header>

      <main className="flex-1 mx-auto w-full max-w-[1400px] px-5 py-6">
        <Routes>
          <Route path="/" element={<Navigate to="/home" replace />} />
          <Route path="/home" element={<HomePage />} />
          <Route path="/shorts" element={<ShortsPage />} />
          <Route path="/transcript" element={<TranscriptPage />} />
          <Route path="/banners" element={<BannersPage />} />
          <Route path="/settings" element={<SettingsPage />} />
        </Routes>
      </main>
    </div>
  )
}
