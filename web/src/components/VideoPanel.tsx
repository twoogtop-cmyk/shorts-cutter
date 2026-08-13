import { useEffect, useRef, useState } from 'react'
import { api, formatBytes, formatTimecode, watchJob, type Job, type Video } from '../api'
import { Button, Panel } from './ui'

// Порядок соответствует стадиям обработки на сервере.
const STAGES: { key: string; title: string }[] = [
  { key: 'uploaded', title: 'Видео загружено' },
  { key: 'audio_extraction', title: 'Аудио извлечено' },
  { key: 'transcribing', title: 'Речь распознана' },
  { key: 'scene_detection', title: 'Сцены определены' },
  { key: 'ai_analysis', title: 'Найдены интересные моменты' },
  { key: 'clips_generation', title: 'Shorts созданы' },
]

function StageList({ job }: { job: Job | null }) {
  const currentIndex = job?.stage ? STAGES.findIndex((s) => s.key === job.stage) : -1
  const finished = job?.status === 'done'

  return (
    <ul className="space-y-1.5">
      {STAGES.map((stage, i) => {
        // Завершённая задача закрывает стадии только до достигнутой:
        // отмечать зелёным то, что не выполнялось, нельзя.
        const done = i < currentIndex || (finished && i <= currentIndex)
        const active = !finished && i === currentIndex
        return (
          <li key={stage.key} className="flex items-center gap-2 text-sm">
            <span
              className={
                done
                  ? 'text-[var(--color-ok)]'
                  : active
                    ? 'text-[var(--color-accent)]'
                    : 'text-neutral-600'
              }
            >
              {done ? '✓' : active ? '●' : '○'}
            </span>
            <span className={done ? 'text-neutral-300' : active ? 'text-white' : 'text-neutral-500'}>
              {stage.title}
            </span>
            {active && job && (
              <span className="text-xs text-neutral-500">{job.progress}%</span>
            )}
          </li>
        )
      })}
    </ul>
  )
}

/** Распознавание тарифицируется поминутно, анализ — примерная оценка. */
function estimateCost(durationSeconds: number | null): { stt: number; ai: number; total: number } {
  const minutes = Math.ceil((durationSeconds ?? 0) / 60)
  const stt = minutes * 2
  const ai = 20
  return { stt, ai, total: stt + ai }
}

export function VideoPanel({
  video,
  balance,
  onChanged,
}: {
  video: Video
  balance: number | null
  onChanged: () => void
}) {
  const [job, setJob] = useState<Job | null>(null)
  const [track, setTrack] = useState(video.audio_track_index ?? 0)
  const [error, setError] = useState<string | null>(null)
  const unwatch = useRef<() => void>(() => {})

  // Подхватываем уже идущую обработку — например, после перезагрузки страницы.
  useEffect(() => {
    let alive = true
    api
      .listJobs(video.id)
      .then((jobs) => {
        if (!alive) return
        const relevant = jobs.find((j) => j.type === 'analyze') ?? null
        setJob(relevant)
        if (relevant && ['queued', 'running'].includes(relevant.status)) {
          unwatch.current = watchJob(relevant.id, (j) => {
            setJob(j)
            if (['done', 'failed', 'canceled'].includes(j.status)) onChanged()
          })
        }
      })
      .catch(() => {})
    return () => {
      alive = false
      unwatch.current()
    }
  }, [video.id])

  const startAnalysis = async () => {
    setError(null)
    try {
      const { job_id } = await api.analyze(video.id, track)
      unwatch.current()
      unwatch.current = watchJob(job_id, (j) => {
        setJob(j)
        if (['done', 'failed', 'canceled'].includes(j.status)) onChanged()
      })
      setJob({ ...(job ?? ({} as Job)), id: job_id, status: 'queued', progress: 0, stage: null } as Job)
    } catch (e) {
      setError(String((e as Error).message ?? e))
    }
  }

  const running = job && ['queued', 'running'].includes(job.status)
  const cost = estimateCost(video.duration)

  return (
    <Panel
      title={video.original_filename}
      actions={
        running ? (
          <Button
            variant="danger"
            onClick={() => api.cancelJob(job!.id).then(onChanged).catch(() => {})}
          >
            Остановить
          </Button>
        ) : (
          <Button variant="primary" onClick={startAnalysis}>
            {job?.status === 'done' ? 'Запустить заново' : 'Начать анализ'}
          </Button>
        )
      }
    >
      <div className="grid lg:grid-cols-2 gap-6">
        <div className="space-y-3">
          <dl className="grid grid-cols-2 gap-x-4 gap-y-2 text-sm">
            <dt className="text-neutral-500">Длительность</dt>
            <dd>{video.duration ? formatTimecode(video.duration) : '—'}</dd>
            <dt className="text-neutral-500">Разрешение</dt>
            <dd>{video.width ? `${video.width}×${video.height}` : '—'}</dd>
            <dt className="text-neutral-500">Частота кадров</dt>
            <dd>{video.fps ? `${video.fps} fps` : '—'}</dd>
            <dt className="text-neutral-500">Кодек</dt>
            <dd>{video.video_codec ?? '—'}</dd>
            <dt className="text-neutral-500">Размер</dt>
            <dd>{formatBytes(video.size_bytes)}</dd>
          </dl>

          {video.audio_tracks.length > 1 && (
            <label className="block pt-2">
              <span className="block text-xs text-neutral-400 mb-1.5">Дорожка звука</span>
              <select
                className="w-full px-3 py-2 rounded-lg bg-[var(--color-panel-2)] border border-[var(--color-line)] text-sm"
                value={track}
                disabled={!!running}
                onChange={(e) => {
                  const value = Number(e.target.value)
                  setTrack(value)
                  api.setAudioTrack(video.id, value).catch(() => {})
                }}
              >
                {video.audio_tracks.map((t) => (
                  <option key={t.index} value={t.index}>
                    №{t.index + 1} · {t.language}
                    {t.title ? ` · ${t.title}` : ''} · {t.codec}
                  </option>
                ))}
              </select>
            </label>
          )}
        </div>

        <div>
          <StageList job={job} />

          {!running && job?.status !== 'done' && (
            <div className="mt-4 rounded-lg bg-[var(--color-panel-2)] px-3 py-2.5 text-xs space-y-1">
              <div className="text-neutral-400">
                Обработка обойдётся примерно в{' '}
                <span className="text-neutral-200">{cost.total} ₽</span>{' '}
                <span className="text-neutral-500">
                  (распознавание {cost.stt} ₽ + анализ ~{cost.ai} ₽)
                </span>
              </div>
              {balance != null && balance < cost.total && (
                <div className="text-[var(--color-warn)]">
                  На счёте {balance.toFixed(0)} ₽ — не хватает {Math.ceil(cost.total - balance)} ₽.
                  Распознавание пройдёт, но на поиск моментов средств не останется.
                  Транскрипция сохранится: после пополнения поиск обойдётся всего в ~{cost.ai} ₽.
                </div>
              )}
            </div>
          )}

          {job?.status === 'failed' && (
            <div className="mt-3 rounded-lg border border-[var(--color-bad)]/40 bg-[var(--color-bad)]/10 px-3 py-2 text-xs text-[#ffb4b8]">
              <div className="mb-2">{job.error}</div>
              <Button
                variant="ghost"
                onClick={() => api.retryJob(job.id).then(onChanged).catch(() => {})}
              >
                Повторить
              </Button>
            </div>
          )}
          {error && <p className="mt-3 text-xs text-[var(--color-bad)]">{error}</p>}
        </div>
      </div>
    </Panel>
  )
}
