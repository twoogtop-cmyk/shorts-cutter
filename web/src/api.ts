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

export type AudioTrack = {
  index: number
  stream_index: number
  language: string
  title: string
  codec: string
  channels: number
}

export type Video = {
  id: number
  original_filename: string
  status: string
  size_bytes: number
  duration: number | null
  width: number | null
  height: number | null
  fps: number | null
  video_codec: string | null
  audio_track_index: number
  audio_tracks: AudioTrack[]
  error: string | null
  created_at: string
}

export type Job = {
  id: number
  video_id: number | null
  type: string
  status: 'queued' | 'running' | 'done' | 'failed' | 'canceled'
  stage: string | null
  stage_title: string | null
  progress: number
  error: string | null
  logs?: { ts: string; level: string; message: string }[]
}

export const api = {
  systemStatus: () => request<SystemStatus>('/api/system/status'),
  getSettings: () => request<Settings>('/api/settings'),
  saveSettings: (values: Settings) =>
    request<Settings>('/api/settings', { method: 'PUT', body: JSON.stringify(values) }),

  listVideos: () => request<Video[]>('/api/videos'),
  getVideo: (id: number) => request<Video>(`/api/videos/${id}`),
  setAudioTrack: (id: number, track: number) =>
    request<{ audio_track: number }>(`/api/videos/${id}/audio-track`, {
      method: 'POST',
      body: JSON.stringify({ audio_track: track }),
    }),
  analyze: (id: number, audioTrack?: number) =>
    request<{ job_id: number }>(`/api/videos/${id}/analyze`, {
      method: 'POST',
      body: JSON.stringify(audioTrack == null ? {} : { audio_track: audioTrack }),
    }),

  activeJobs: () => request<Job[]>('/api/jobs/active'),
  listJobs: (videoId?: number) =>
    request<Job[]>(`/api/jobs${videoId ? `?video_id=${videoId}` : ''}`),
  getJob: (id: number) => request<Job>(`/api/jobs/${id}`),
  cancelJob: (id: number) => request<{ ok: boolean }>(`/api/jobs/${id}/cancel`, { method: 'POST' }),
  retryJob: (id: number) => request<{ job_id: number }>(`/api/jobs/${id}/retry`, { method: 'POST' }),
}

const CHUNK_SIZE = 4 * 1024 * 1024

export type UploadHandle = { cancel: () => void }

/**
 * Загружает файл кусками. При обрыве спрашивает у сервера, сколько байт
 * реально принято, и продолжает с этого места — многогигабайтный файл
 * не приходится слать заново.
 */
export async function uploadVideo(
  file: File,
  onProgress: (sent: number, total: number) => void,
  onStage: (text: string) => void,
  handle?: UploadHandle,
): Promise<{ video_id: number; job_id: number }> {
  let cancelled = false
  if (handle) handle.cancel = () => (cancelled = true)

  onStage('Готовим загрузку…')
  const init = await request<{ upload_id: string; video_id: number }>('/api/videos/upload/init', {
    method: 'POST',
    body: JSON.stringify({ filename: file.name, total_size: file.size }),
  })

  let offset = 0
  let failures = 0
  onStage('Загружаем файл…')

  while (offset < file.size) {
    if (cancelled) throw new Error('Загрузка отменена')
    const slice = file.slice(offset, Math.min(offset + CHUNK_SIZE, file.size))
    try {
      const resp = await fetch(`/api/videos/upload/${init.upload_id}/chunk?offset=${offset}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/octet-stream' },
        body: slice,
      })
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`)
      const data = (await resp.json()) as { received: number }
      offset = data.received
      failures = 0
      onProgress(offset, file.size)
    } catch {
      failures += 1
      if (failures > 5) throw new Error('Связь потеряна, загрузка прервана')
      onStage(`Обрыв связи, продолжаем (попытка ${failures})…`)
      await new Promise((r) => setTimeout(r, 1500 * failures))
      const status = await request<{ received: number }>(
        `/api/videos/upload/${init.upload_id}/status`,
      )
      offset = status.received
      onProgress(offset, file.size)
    }
  }

  onStage('Проверяем файл…')
  return request<{ video_id: number; job_id: number }>(
    `/api/videos/upload/${init.upload_id}/complete`,
    { method: 'POST' },
  )
}

/** Подписка на прогресс задачи через SSE с откатом на опрос при сбое. */
export function watchJob(jobId: number, onUpdate: (job: Job) => void): () => void {
  const source = new EventSource(`/api/jobs/${jobId}/stream`)
  let closed = false
  let timer: number | undefined

  source.onmessage = (event) => {
    try {
      const job = JSON.parse(event.data) as Job
      onUpdate(job)
      if (['done', 'failed', 'canceled'].includes(job.status)) {
        closed = true
        source.close()
      }
    } catch {
      /* пропускаем некорректный кадр */
    }
  }
  source.onerror = () => {
    source.close()
    if (closed) return
    timer = window.setInterval(async () => {
      try {
        const job = await api.getJob(jobId)
        onUpdate(job)
        if (['done', 'failed', 'canceled'].includes(job.status)) {
          window.clearInterval(timer)
        }
      } catch {
        /* сервер недоступен — повторим на следующем тике */
      }
    }, 2000)
  }

  return () => {
    closed = true
    source.close()
    if (timer) window.clearInterval(timer)
  }
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
