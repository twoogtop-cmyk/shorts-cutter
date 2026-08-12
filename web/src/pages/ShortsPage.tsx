import { useCallback, useEffect, useRef, useState } from 'react'
import {
  api,
  formatTimecode,
  watchJob,
  type Candidate,
  type Job,
  type Video,
} from '../api'
import { Button, ErrorBox, Panel } from '../components/ui'

const FILTERS = [
  { key: 'all', label: 'Все' },
  { key: 'candidate', label: 'Новые' },
  { key: 'approved', label: 'Одобрено' },
  { key: 'rejected', label: 'Отклонено' },
]

function ScoreBar({ label, value }: { label: string; value: number | null }) {
  const v = value ?? 0
  const color = v >= 80 ? 'var(--color-ok)' : v >= 60 ? 'var(--color-warn)' : 'var(--color-bad)'
  return (
    <div className="flex items-center gap-2">
      <span className="text-xs text-neutral-500 w-[86px] shrink-0">{label}</span>
      <div className="flex-1 h-1.5 rounded-full bg-[var(--color-panel-2)] overflow-hidden">
        <div className="h-full rounded-full" style={{ width: `${v}%`, background: color }} />
      </div>
      <span className="text-xs tabular-nums w-7 text-right text-neutral-400">{v}</span>
    </div>
  )
}

/** Проигрывает нужный отрезок исходника: превью-файлы появятся на этапе рендера. */
function RangePlayer({ src, start, end }: { src: string; start: number; end: number }) {
  const ref = useRef<HTMLVideoElement>(null)

  useEffect(() => {
    const player = ref.current
    if (!player) return
    const onLoaded = () => {
      player.currentTime = start
    }
    const onTime = () => {
      if (player.currentTime >= end) player.pause()
    }
    player.addEventListener('loadedmetadata', onLoaded)
    player.addEventListener('timeupdate', onTime)
    return () => {
      player.removeEventListener('loadedmetadata', onLoaded)
      player.removeEventListener('timeupdate', onTime)
    }
  }, [start, end])

  return (
    <video
      ref={ref}
      controls
      preload="metadata"
      className="w-full rounded-lg bg-black aspect-video"
      src={`${encodeURI(src)}#t=${start}`}
    />
  )
}

export function ShortsPage() {
  const [videos, setVideos] = useState<Video[]>([])
  const [videoId, setVideoId] = useState<number | null>(null)
  const [items, setItems] = useState<Candidate[]>([])
  const [filter, setFilter] = useState('all')
  const [error, setError] = useState<string | null>(null)
  const [job, setJob] = useState<Job | null>(null)
  const unwatch = useRef<() => void>(() => {})

  const load = useCallback((id: number) => {
    api
      .listCandidates(id)
      .then(setItems)
      .catch((e) => setError(String(e.message ?? e)))
  }, [])

  useEffect(() => {
    api
      .listVideos()
      .then((list) => {
        setVideos(list)
        if (list.length) setVideoId((prev) => prev ?? list[0].id)
      })
      .catch((e) => setError(String(e.message ?? e)))
    return () => unwatch.current()
  }, [])

  useEffect(() => {
    if (videoId != null) load(videoId)
  }, [videoId, load])

  const video = videos.find((v) => v.id === videoId) ?? null
  const shown = filter === 'all' ? items : items.filter((c) => c.status === filter)

  const setStatus = async (candidate: Candidate, status: string) => {
    setItems((prev) => prev.map((c) => (c.id === candidate.id ? { ...c, status } : c)))
    try {
      await api.updateCandidate(candidate.id, { status } as Partial<Candidate>)
    } catch (e) {
      setError(String((e as Error).message ?? e))
      if (videoId != null) load(videoId)
    }
  }

  const runSearch = async () => {
    if (videoId == null) return
    setError(null)
    try {
      const { job_id } = await api.findMoments(videoId)
      unwatch.current()
      unwatch.current = watchJob(job_id, (j) => {
        setJob(j)
        if (j.status === 'done') load(videoId)
      })
      setJob({ id: job_id, status: 'queued', progress: 0 } as Job)
    } catch (e) {
      setError(String((e as Error).message ?? e))
    }
  }

  if (!videos.length) {
    return (
      <Panel title="Шортсы">
        <p className="text-sm text-neutral-400">Сначала загрузите серию на главной странице.</p>
      </Panel>
    )
  }

  const running = job && ['queued', 'running'].includes(job.status)

  return (
    <div className="space-y-5">
      <ErrorBox error={error} />

      <Panel
        title={`Найдено моментов: ${items.length}`}
        actions={
          <div className="flex items-center gap-2">
            {videos.length > 1 && (
              <select
                className="px-3 py-1.5 rounded-lg bg-[var(--color-panel-2)] border border-[var(--color-line)] text-sm"
                value={videoId ?? ''}
                onChange={(e) => setVideoId(Number(e.target.value))}
              >
                {videos.map((v) => (
                  <option key={v.id} value={v.id}>
                    {v.original_filename}
                  </option>
                ))}
              </select>
            )}
            <Button variant="primary" onClick={runSearch} disabled={!!running}>
              {running ? `Ищем… ${job?.progress ?? 0}%` : 'Искать моменты'}
            </Button>
          </div>
        }
      >
        <div className="flex gap-1.5">
          {FILTERS.map((f) => {
            const count =
              f.key === 'all' ? items.length : items.filter((c) => c.status === f.key).length
            return (
              <button
                key={f.key}
                onClick={() => setFilter(f.key)}
                className={`px-3 py-1.5 rounded-lg text-sm transition-colors ${
                  filter === f.key
                    ? 'bg-[var(--color-panel-2)] text-white'
                    : 'text-neutral-400 hover:text-white'
                }`}
              >
                {f.label} <span className="text-neutral-500">{count}</span>
              </button>
            )
          })}
        </div>

        {job?.status === 'failed' && (
          <p className="mt-3 text-xs text-[var(--color-bad)]">{job.error}</p>
        )}
      </Panel>

      {shown.map((candidate) => (
        <Panel
          key={candidate.id}
          title={
            <span className="flex items-center gap-2">
              <span
                className={`text-sm font-semibold ${
                  (candidate.total_score ?? 0) >= 80
                    ? 'text-[var(--color-ok)]'
                    : 'text-[var(--color-warn)]'
                }`}
              >
                {candidate.total_score ?? '—'}
              </span>
              <span className="text-neutral-200">{candidate.title || 'Без названия'}</span>
              {candidate.status === 'approved' && (
                <span className="text-xs text-[var(--color-ok)]">одобрен</span>
              )}
              {candidate.status === 'rejected' && (
                <span className="text-xs text-[var(--color-bad)]">отклонён</span>
              )}
            </span>
          }
          actions={
            <div className="flex gap-2">
              <Button variant="ok" onClick={() => setStatus(candidate, 'approved')}>
                Одобрить
              </Button>
              <Button variant="danger" onClick={() => setStatus(candidate, 'rejected')}>
                Отклонить
              </Button>
            </div>
          }
        >
          <div className="grid lg:grid-cols-[400px_1fr] gap-6">
            <div className="space-y-3">
              {video?.media_url && (
                <RangePlayer src={video.media_url} start={candidate.start} end={candidate.end} />
              )}
              <div className="text-xs text-neutral-400">
                {formatTimecode(candidate.start)} → {formatTimecode(candidate.end)} ·{' '}
                {Math.round(candidate.duration)} сек · {candidate.category}
              </div>
              <div className="space-y-1.5">
                <ScoreBar label="Зацепка" value={candidate.hook_score} />
                <ScoreBar label="Удержание" value={candidate.retention_score} />
                <ScoreBar label="Понятность" value={candidate.context_score} />
                <ScoreBar label="Эмоции" value={candidate.emotion_score} />
                <ScoreBar label="Финал" value={candidate.ending_score} />
              </div>
            </div>

            <div className="space-y-3 min-w-0">
              {candidate.ai_reason && (
                <p className="text-sm text-neutral-300 leading-relaxed">{candidate.ai_reason}</p>
              )}
              {candidate.transcript_text && (
                <pre className="text-xs text-neutral-400 whitespace-pre-wrap font-sans leading-relaxed max-h-[280px] overflow-y-auto">
                  {candidate.transcript_text}
                </pre>
              )}
            </div>
          </div>
        </Panel>
      ))}

      {shown.length === 0 && (
        <p className="text-sm text-neutral-500 px-1">
          {items.length === 0
            ? 'Моментов пока нет — распознайте речь и нажмите «Искать моменты».'
            : 'В этой категории пусто.'}
        </p>
      )}
    </div>
  )
}
