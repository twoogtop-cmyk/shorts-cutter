import { useCallback, useEffect, useState } from 'react'
import { api, formatBytes, type SystemStatus, type Video } from '../api'
import { ErrorBox, Panel, Stat } from '../components/ui'
import { UploadZone } from '../components/UploadZone'
import { VideoPanel } from '../components/VideoPanel'

export function HomePage() {
  const [status, setStatus] = useState<SystemStatus | null>(null)
  const [videos, setVideos] = useState<Video[]>([])
  const [error, setError] = useState<string | null>(null)

  const reload = useCallback(() => {
    api.systemStatus().then(setStatus).catch((e) => setError(String(e.message ?? e)))
    api.listVideos().then(setVideos).catch((e) => setError(String(e.message ?? e)))
  }, [])

  useEffect(() => {
    reload()
    const timer = setInterval(reload, 15000)
    return () => clearInterval(timer)
  }, [reload])

  const free = status?.disk.free ?? 0
  const diskTone = free < 1.5 * 1024 ** 3 ? 'bad' : free < 3 * 1024 ** 3 ? 'warn' : 'ok'
  const balance = status?.balance
  const balanceTone = balance == null ? 'warn' : balance < 150 ? 'bad' : balance < 400 ? 'warn' : 'ok'

  return (
    <div className="space-y-5">
      <ErrorBox error={error} />

      <Panel title="Состояние системы">
        <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
          <Stat label="Свободно на диске" value={formatBytes(free)} tone={diskTone} />
          <Stat label="Занято" value={status ? formatBytes(status.disk.used) : '—'} />
          <Stat
            label="Максимум для серии"
            value={status ? formatBytes(status.max_source_bytes) : '—'}
          />
          <Stat
            label="Баланс gen-api"
            value={balance == null ? '—' : `${balance.toFixed(2)} ₽`}
            tone={balanceTone}
          />
        </div>

        {status?.balance_error && (
          <p className="mt-3 text-xs text-[var(--color-warn)]">
            Не удалось получить баланс: {status.balance_error}
          </p>
        )}
        {balance != null && balance < 150 && (
          <p className="mt-3 text-xs text-[var(--color-warn)]">
            Баланса хватит примерно на {Math.floor(balance / 2)} минут распознавания речи.
            Обработка часовой серии стоит около 150 ₽.
          </p>
        )}
      </Panel>

      <Panel title="Загрузка серии">
        <UploadZone maxBytes={status?.max_source_bytes ?? 0} onUploaded={reload} />
      </Panel>

      {videos.map((video) => (
        <VideoPanel key={video.id} video={video} onChanged={reload} />
      ))}

      {videos.length === 0 && (
        <p className="text-sm text-neutral-500 px-1">
          Загруженных серий пока нет.
        </p>
      )}
    </div>
  )
}
