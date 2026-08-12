import { useEffect, useMemo, useRef, useState } from 'react'
import {
  api,
  formatTimecode,
  watchJob,
  type Job,
  type Segment,
  type Transcript,
  type Video,
} from '../api'
import { Button, ErrorBox, Panel, inputClass } from '../components/ui'

const SPEAKER_COLORS = ['#4f8cff', '#f5a524', '#35c46b', '#c77dff', '#ff7a90', '#4dd0e1']

function speakerColor(speaker: string | null, speakers: string[]): string {
  if (!speaker) return '#8b93a7'
  const i = speakers.indexOf(speaker)
  return SPEAKER_COLORS[i >= 0 ? i % SPEAKER_COLORS.length : 0]
}

function speakerLabel(speaker: string | null): string {
  if (!speaker) return '—'
  const m = speaker.match(/(\d+)/)
  return m ? `Голос ${Number(m[1]) + 1}` : speaker
}

export function TranscriptPage() {
  const [videos, setVideos] = useState<Video[]>([])
  const [videoId, setVideoId] = useState<number | null>(null)
  const [transcript, setTranscript] = useState<Transcript | null>(null)
  const [search, setSearch] = useState('')
  const [found, setFound] = useState<Segment[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [sampleStart, setSampleStart] = useState('20')
  const [sampleMinutes, setSampleMinutes] = useState('3')
  const [job, setJob] = useState<Job | null>(null)
  const playerRef = useRef<HTMLVideoElement>(null)
  const unwatch = useRef<() => void>(() => {})

  useEffect(() => {
    api
      .listVideos()
      .then((list) => {
        setVideos(list)
        if (list.length && videoId == null) setVideoId(list[0].id)
      })
      .catch((e) => setError(String(e.message ?? e)))
    return () => unwatch.current()
  }, [])

  const loadTranscript = (id: number) => {
    api
      .getTranscript(id)
      .then(setTranscript)
      .catch((e) => setError(String(e.message ?? e)))
  }

  useEffect(() => {
    if (videoId != null) loadTranscript(videoId)
  }, [videoId])

  const video = useMemo(() => videos.find((v) => v.id === videoId) ?? null, [videos, videoId])
  const rows = found ?? transcript?.segments ?? []
  const speakers = transcript?.speakers ?? []

  const seek = (seconds: number) => {
    const player = playerRef.current
    if (!player) return
    player.currentTime = Math.max(0, seconds - 0.3)
    player.play().catch(() => {})
  }

  const runSample = async () => {
    if (videoId == null) return
    setError(null)
    try {
      const res = await api.transcribeSample(
        videoId,
        Number(sampleStart) * 60,
        Number(sampleMinutes),
      )
      unwatch.current()
      unwatch.current = watchJob(res.job_id, (j) => {
        setJob(j)
        if (j.status === 'done') loadTranscript(videoId)
      })
      setJob({ id: res.job_id, status: 'queued', progress: 0 } as Job)
    } catch (e) {
      setError(String((e as Error).message ?? e))
    }
  }

  const doSearch = async (value: string) => {
    setSearch(value)
    if (videoId == null || value.trim().length < 2) {
      setFound(null)
      return
    }
    try {
      const res = await api.searchTranscript(videoId, value)
      setFound(res.results)
    } catch {
      setFound([])
    }
  }

  if (!videos.length) {
    return (
      <Panel title="Транскрипция">
        <p className="text-sm text-neutral-400">Сначала загрузите серию на главной странице.</p>
      </Panel>
    )
  }

  return (
    <div className="space-y-5">
      <ErrorBox error={error} />

      <Panel
        title="Транскрипция"
        actions={
          videos.length > 1 ? (
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
          ) : null
        }
      >
        <div className="grid lg:grid-cols-[420px_1fr] gap-6">
          <div className="space-y-4">
            {video?.media_url && (
              <video
                ref={playerRef}
                controls
                preload="metadata"
                className="w-full rounded-lg bg-black"
                src={encodeURI(video.media_url)}
              />
            )}

            <div className="rounded-lg bg-[var(--color-panel-2)] p-4 space-y-3">
              <div className="text-xs text-neutral-400">
                Пробное распознавание фрагмента — проверить качество, не оплачивая серию целиком
              </div>
              <div className="flex items-end gap-2">
                <label className="flex-1">
                  <span className="block text-xs text-neutral-500 mb-1">с минуты</span>
                  <input
                    className={inputClass}
                    type="number"
                    min={0}
                    value={sampleStart}
                    onChange={(e) => setSampleStart(e.target.value)}
                  />
                </label>
                <label className="flex-1">
                  <span className="block text-xs text-neutral-500 mb-1">минут</span>
                  <input
                    className={inputClass}
                    type="number"
                    min={1}
                    max={15}
                    value={sampleMinutes}
                    onChange={(e) => setSampleMinutes(e.target.value)}
                  />
                </label>
                <Button variant="primary" onClick={runSample} disabled={job?.status === 'running'}>
                  Распознать
                </Button>
              </div>
              <div className="text-xs text-neutral-500">
                Стоимость примерно {Math.round(Number(sampleMinutes) * 2)} ₽
              </div>
              {job && ['queued', 'running'].includes(job.status) && (
                <div className="text-xs text-[var(--color-accent)]">
                  Распознаём… {job.progress}%
                </div>
              )}
              {job?.status === 'failed' && (
                <div className="text-xs text-[var(--color-bad)]">{job.error}</div>
              )}
            </div>

            {speakers.length > 0 && (
              <div className="flex flex-wrap gap-2">
                {speakers.map((s) => (
                  <span
                    key={s}
                    className="text-xs px-2 py-1 rounded-md"
                    style={{
                      color: speakerColor(s, speakers),
                      background: `${speakerColor(s, speakers)}1a`,
                    }}
                  >
                    {speakerLabel(s)}
                  </span>
                ))}
              </div>
            )}
          </div>

          <div className="space-y-3 min-w-0">
            <input
              className={inputClass}
              placeholder="Поиск по репликам…"
              value={search}
              onChange={(e) => doSearch(e.target.value)}
            />

            <div className="text-xs text-neutral-500">
              {found
                ? `Найдено реплик: ${found.length}`
                : `Реплик всего: ${transcript?.count ?? 0}`}
            </div>

            <div className="max-h-[60vh] overflow-y-auto pr-1 space-y-0.5">
              {rows.map((segment) => (
                <button
                  key={segment.id}
                  onClick={() => seek(segment.start)}
                  className="w-full text-left px-3 py-2 rounded-lg hover:bg-[var(--color-panel-2)] transition-colors flex gap-3"
                >
                  <span className="text-xs text-neutral-500 tabular-nums pt-0.5 shrink-0">
                    {formatTimecode(segment.start)}
                  </span>
                  <span
                    className="text-xs shrink-0 pt-0.5 w-[68px] truncate"
                    style={{ color: speakerColor(segment.speaker, speakers) }}
                  >
                    {speakerLabel(segment.speaker)}
                  </span>
                  <span className="text-sm text-neutral-200">{segment.text}</span>
                </button>
              ))}

              {rows.length === 0 && (
                <p className="text-sm text-neutral-500 px-3 py-4">
                  {found
                    ? 'Ничего не найдено.'
                    : 'Транскрипции пока нет — запустите пробное распознавание или полный анализ.'}
                </p>
              )}
            </div>
          </div>
        </div>
      </Panel>
    </div>
  )
}
