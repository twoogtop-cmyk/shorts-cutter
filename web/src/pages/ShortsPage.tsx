import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  api,
  formatTimecode,
  watchJob,
  type Banner,
  type Candidate,
  type Job,
  type Segment,
  type Video,
} from '../api'
import { Button, ErrorBox, Panel } from '../components/ui'
import { ClipEditor } from '../components/ClipEditor'

const FILTERS = [
  { key: 'all', label: 'Все' },
  { key: 'candidate', label: 'Новые' },
  { key: 'approved', label: 'Одобрено' },
  { key: 'rejected', label: 'Отклонено' },
  { key: 'ready', label: 'Готовы к скачиванию' },
]

const STATUS_LABELS: Record<string, { text: string; color: string }> = {
  approved: { text: 'одобрен', color: 'var(--color-ok)' },
  rejected: { text: 'отклонён', color: 'var(--color-bad)' },
  rendering: { text: 'рендерится', color: 'var(--color-accent)' },
  ready: { text: 'готов', color: 'var(--color-ok)' },
  downloaded: { text: 'скачан', color: 'var(--color-accent)' },
}

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

/** Показывает готовый шортс, а пока его нет — нужный отрезок исходника. */
function ClipPlayer({
  candidate,
  sourceUrl,
}: {
  candidate: Candidate
  sourceUrl: string | null
}) {
  const ref = useRef<HTMLVideoElement>(null)
  const clip = candidate.render_url || candidate.preview_url

  useEffect(() => {
    const player = ref.current
    if (!player || clip) return
    const onLoaded = () => {
      player.currentTime = candidate.start
    }
    const onTime = () => {
      if (player.currentTime >= candidate.end) player.pause()
    }
    player.addEventListener('loadedmetadata', onLoaded)
    player.addEventListener('timeupdate', onTime)
    return () => {
      player.removeEventListener('loadedmetadata', onLoaded)
      player.removeEventListener('timeupdate', onTime)
    }
  }, [clip, candidate.start, candidate.end])

  if (clip) {
    return (
      <video
        ref={ref}
        controls
        preload="metadata"
        className="w-full max-w-[260px] rounded-lg bg-black aspect-[9/16] mx-auto"
        src={encodeURI(clip)}
      />
    )
  }

  if (!sourceUrl) return null
  return (
    <div className="space-y-1.5">
      <video
        ref={ref}
        controls
        preload="metadata"
        className="w-full rounded-lg bg-black aspect-video"
        src={`${encodeURI(sourceUrl)}#t=${candidate.start}`}
      />
      <p className="text-xs text-neutral-500">Фрагмент исходника — вертикальный ролик ещё не готов</p>
    </div>
  )
}

export function ShortsPage() {
  const [videos, setVideos] = useState<Video[]>([])
  const [videoId, setVideoId] = useState<number | null>(null)
  const [items, setItems] = useState<Candidate[]>([])
  const [filter, setFilter] = useState('all')
  const [selected, setSelected] = useState<Set<number>>(new Set())
  const [error, setError] = useState<string | null>(null)
  const [job, setJob] = useState<Job | null>(null)
  const [busy, setBusy] = useState(false)
  const [banners, setBanners] = useState<Banner[]>([])
  const [segments, setSegments] = useState<Segment[]>([])
  const [editing, setEditing] = useState<Candidate | null>(null)
  const [activeIndex, setActiveIndex] = useState(0)
  const unwatch = useRef<() => void>(() => {})
  const cardRefs = useRef<Record<number, HTMLDivElement | null>>({})

  const load = useCallback((id: number) => {
    api.listCandidates(id).then(setItems).catch((e) => setError(String(e.message ?? e)))
  }, [])

  useEffect(() => {
    api
      .listVideos()
      .then((list) => {
        setVideos(list)
        if (list.length) setVideoId((prev) => prev ?? list[0].id)
      })
      .catch((e) => setError(String(e.message ?? e)))
    api.listBanners().then(setBanners).catch(() => {})
    return () => unwatch.current()
  }, [])

  useEffect(() => {
    if (videoId == null) return
    load(videoId)
    api
      .getTranscript(videoId)
      .then((t) => setSegments(t.segments))
      .catch(() => setSegments([]))
  }, [videoId, load])

  const video = videos.find((v) => v.id === videoId) ?? null
  const shown = useMemo(
    () => (filter === 'all' ? items : items.filter((c) => c.status === filter)),
    [items, filter],
  )

  const allShownSelected = shown.length > 0 && shown.every((c) => selected.has(c.id))

  // Быстрая модерация с клавиатуры: смотреть десятки роликов мышью долго.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const target = e.target as HTMLElement | null
      if (editing) return
      if (target && ['INPUT', 'TEXTAREA', 'SELECT'].includes(target.tagName)) return
      if (e.metaKey || e.ctrlKey || e.altKey) return

      const current = shown[activeIndex]
      const move = (delta: number) => {
        const next = Math.max(0, Math.min(shown.length - 1, activeIndex + delta))
        setActiveIndex(next)
        const card = shown[next] && cardRefs.current[shown[next].id]
        card?.scrollIntoView({ behavior: 'smooth', block: 'center' })
      }

      switch (e.key.toLowerCase()) {
        case 'arrowdown':
        case 'arrowright':
          e.preventDefault()
          move(1)
          break
        case 'arrowup':
        case 'arrowleft':
          e.preventDefault()
          move(-1)
          break
        case ' ': {
          e.preventDefault()
          if (!current) break
          const card = cardRefs.current[current.id]
          const player = card?.querySelector('video')
          if (player) player.paused ? player.play() : player.pause()
          break
        }
        case 'a':
        case 'ф':
          if (current) setStatus(current, 'approved')
          break
        case 'r':
        case 'к':
          if (current) setStatus(current, 'rejected')
          break
        case 'e':
        case 'у':
          if (current) setEditing(current)
          break
        case 'd':
        case 'в':
          if (current?.render_url) window.location.href = api.downloadUrl(current.id)
          break
        case 'x':
        case 'ч':
          if (current) toggle(current.id)
          break
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [shown, activeIndex, editing])

  useEffect(() => {
    if (activeIndex >= shown.length) setActiveIndex(Math.max(0, shown.length - 1))
  }, [shown.length, activeIndex])

  const toggle = (id: number) => {
    setSelected((prev) => {
      const next = new Set(prev)
      next.has(id) ? next.delete(id) : next.add(id)
      return next
    })
  }

  const toggleAll = () => {
    setSelected((prev) => {
      if (allShownSelected) {
        const next = new Set(prev)
        shown.forEach((c) => next.delete(c.id))
        return next
      }
      return new Set([...prev, ...shown.map((c) => c.id)])
    })
  }

  const watch = (jobId: number) => {
    unwatch.current()
    unwatch.current = watchJob(jobId, (j) => {
      setJob(j)
      if (['done', 'failed', 'canceled'].includes(j.status) && videoId != null) load(videoId)
    })
    setJob({ id: jobId, status: 'queued', progress: 0 } as Job)
  }

  const setStatus = async (candidate: Candidate, status: string) => {
    setItems((prev) => prev.map((c) => (c.id === candidate.id ? { ...c, status } : c)))
    try {
      await api.updateCandidate(candidate.id, { status } as Partial<Candidate>)
    } catch (e) {
      setError(String((e as Error).message ?? e))
      if (videoId != null) load(videoId)
    }
  }

  const withBusy = async (fn: () => Promise<unknown>) => {
    setBusy(true)
    setError(null)
    try {
      await fn()
    } catch (e) {
      setError(String((e as Error).message ?? e))
    } finally {
      setBusy(false)
      if (videoId != null) load(videoId)
    }
  }

  const ids = [...selected]

  const bulkStatus = (status: string) =>
    withBusy(async () => {
      await api.bulkCandidates(ids, status)
      setSelected(new Set())
    })

  const bulkDelete = () =>
    withBusy(async () => {
      if (!confirm(`Удалить выбранные шортсы (${ids.length})? Файлы будут стёрты.`)) return
      for (const id of ids) await api.deleteCandidate(id)
      setSelected(new Set())
    })

  const bulkRender = () =>
    withBusy(async () => {
      const res = await api.renderBulk(ids)
      if (res.job_ids.length) watch(res.job_ids[res.job_ids.length - 1])
    })

  const bulkDownload = async () => {
    const ready = items.filter((c) => selected.has(c.id) && c.render_url)
    if (!ready.length) {
      setError('Среди выбранных нет готовых файлов — сначала нажмите «Отрендерить»')
      return
    }
    const response = await fetch('/api/candidates/download-zip', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ids: ready.map((c) => c.id) }),
    })
    if (!response.ok) {
      setError(await response.text())
      return
    }
    const blob = await response.blob()
    const url = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    link.download = 'shorts.zip'
    link.click()
    URL.revokeObjectURL(url)
    if (videoId != null) load(videoId)
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
            <Button
              variant="primary"
              onClick={() =>
                videoId != null &&
                withBusy(async () => watch((await api.findMoments(videoId)).job_id))
              }
              disabled={!!running || busy}
            >
              {running ? `Работаем… ${job?.progress ?? 0}%` : 'Искать моменты'}
            </Button>
          </div>
        }
      >
        <div className="flex flex-wrap items-center gap-1.5">
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

        {running && (
          <p className="mt-3 text-xs text-[var(--color-accent)]">
            {job?.stage_title ?? 'Обработка'} · {job?.progress ?? 0}%
          </p>
        )}
        {job?.status === 'failed' && (
          <p className="mt-3 text-xs text-[var(--color-bad)]">{job.error}</p>
        )}
      </Panel>

      {shown.length > 0 && (
        <div className="sticky top-2 z-10 rounded-xl border border-[var(--color-line)] bg-[var(--color-panel)]/95 backdrop-blur px-4 py-3 flex flex-wrap items-center gap-3">
          <label className="flex items-center gap-2 text-sm cursor-pointer select-none">
            <input
              type="checkbox"
              className="w-4 h-4 accent-[var(--color-accent)]"
              checked={allShownSelected}
              onChange={toggleAll}
            />
            Выбрать все
          </label>
          <span className="text-xs text-neutral-500">Выбрано: {selected.size}</span>

          <div className="flex flex-wrap gap-2 ml-auto">
            <Button variant="ok" onClick={() => bulkStatus('approved')} disabled={!ids.length || busy}>
              Одобрить
            </Button>
            <Button variant="default" onClick={bulkRender} disabled={!ids.length || busy}>
              Отрендерить
            </Button>
            <Button variant="primary" onClick={bulkDownload} disabled={!ids.length || busy}>
              Скачать ZIP
            </Button>
            <Button variant="danger" onClick={bulkDelete} disabled={!ids.length || busy}>
              Удалить
            </Button>
          </div>
        </div>
      )}

      {shown.map((candidate, index) => {
        const badge = STATUS_LABELS[candidate.status]
        return (
          <div
            key={candidate.id}
            ref={(el) => {
              cardRefs.current[candidate.id] = el
            }}
            onClick={() => setActiveIndex(index)}
            className={`rounded-xl transition-shadow ${
              index === activeIndex ? 'ring-2 ring-[var(--color-accent)]/60' : ''
            }`}
          >
          <Panel
            title={
              <span className="flex items-center gap-2">
                <input
                  type="checkbox"
                  className="w-4 h-4 accent-[var(--color-accent)]"
                  checked={selected.has(candidate.id)}
                  onChange={() => toggle(candidate.id)}
                />
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
                {badge && (
                  <span className="text-xs" style={{ color: badge.color }}>
                    {badge.text}
                  </span>
                )}
              </span>
            }
            actions={
              <div className="flex flex-wrap gap-2">
                <Button variant="ok" onClick={() => setStatus(candidate, 'approved')}>
                  Одобрить
                </Button>
                <Button variant="danger" onClick={() => setStatus(candidate, 'rejected')}>
                  Отклонить
                </Button>
                <Button variant="default" onClick={() => setEditing(candidate)}>
                  Редактировать
                </Button>
                {candidate.render_url ? (
                  <a href={api.downloadUrl(candidate.id)} download>
                    <Button variant="primary">Скачать</Button>
                  </a>
                ) : (
                  <Button
                    variant="default"
                    onClick={() =>
                      withBusy(async () => watch((await api.renderFinal(candidate.id)).job_id))
                    }
                    disabled={busy || candidate.status === 'rendering'}
                  >
                    Отрендерить
                  </Button>
                )}
              </div>
            }
          >
            <div className="grid lg:grid-cols-[320px_1fr] gap-6">
              <div className="space-y-3">
                <ClipPlayer candidate={candidate} sourceUrl={video?.media_url ?? null} />
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
                  <pre className="text-xs text-neutral-400 whitespace-pre-wrap font-sans leading-relaxed max-h-[260px] overflow-y-auto">
                    {candidate.transcript_text}
                  </pre>
                )}
              </div>
            </div>
          </Panel>
          </div>
        )
      })}

      {shown.length === 0 && (
        <p className="text-sm text-neutral-500 px-1">
          {items.length === 0
            ? 'Моментов пока нет — распознайте речь и нажмите «Искать моменты».'
            : 'В этой категории пусто.'}
        </p>
      )}

      {shown.length > 0 && (
        <p className="text-xs text-neutral-600 px-1 pb-4">
          Горячие клавиши: ↑↓ — переход, Пробел — воспроизведение, A — одобрить, R — отклонить,
          E — редактировать, D — скачать, X — отметить галочкой
        </p>
      )}

      {editing && (
        <ClipEditor
          candidate={editing}
          segments={segments}
          banners={banners}
          onClose={() => setEditing(null)}
          onSaved={() => videoId != null && load(videoId)}
        />
      )}
    </div>
  )
}
