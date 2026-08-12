export type DiskInfo = { total: number; used: number; free: number }

export type SystemStatus = {
  disk: DiskInfo
  max_source_bytes: number
  balance: number | null
  balance_error: string | null
}

export type Settings = Record<string, string>

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const resp = await fetch(path, {
    headers: init?.body ? { 'Content-Type': 'application/json' } : undefined,
    ...init,
  })
  if (!resp.ok) {
    const text = await resp.text().catch(() => '')
    throw new Error(text ? `${resp.status}: ${text.slice(0, 300)}` : `HTTP ${resp.status}`)
  }
  return resp.json() as Promise<T>
}

export const api = {
  systemStatus: () => request<SystemStatus>('/api/system/status'),
  getSettings: () => request<Settings>('/api/settings'),
  saveSettings: (values: Settings) =>
    request<Settings>('/api/settings', { method: 'PUT', body: JSON.stringify(values) }),
}

export function formatBytes(bytes: number): string {
  if (!bytes) return '0 Б'
  const units = ['Б', 'КБ', 'МБ', 'ГБ', 'ТБ']
  const i = Math.min(units.length - 1, Math.floor(Math.log(bytes) / Math.log(1024)))
  const value = bytes / Math.pow(1024, i)
  return `${value.toFixed(value >= 10 || i === 0 ? 0 : 1)} ${units[i]}`
}

export function formatTimecode(seconds: number, withMs = false): string {
  const total = Math.max(0, seconds)
  const h = Math.floor(total / 3600)
  const m = Math.floor((total % 3600) / 60)
  const s = Math.floor(total % 60)
  const pad = (n: number) => String(n).padStart(2, '0')
  const base = h > 0 ? `${pad(h)}:${pad(m)}:${pad(s)}` : `${pad(m)}:${pad(s)}`
  if (!withMs) return base
  return `${base}.${String(Math.floor((total % 1) * 1000)).padStart(3, '0')}`
}
