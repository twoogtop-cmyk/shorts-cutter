import { useEffect, useState } from 'react'
import { api, formatBytes, type SystemStatus } from '../api'
import { ErrorBox, Panel, Stat } from '../components/ui'

export function HomePage() {
  const [status, setStatus] = useState<SystemStatus | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let alive = true
    const load = () => {
      api
        .systemStatus()
        .then((s) => alive && (setStatus(s), setError(null)))
        .catch((e) => alive && setError(String(e.message ?? e)))
    }
    load()
    const timer = setInterval(load, 15000)
    return () => {
      alive = false
      clearInterval(timer)
    }
  }, [])

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
          <Stat
            label="Занято"
            value={status ? formatBytes(status.disk.used) : '—'}
          />
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
        <p className="text-sm text-neutral-400">
          Загрузка видео и запуск обработки подключаются на следующем этапе.
        </p>
      </Panel>
    </div>
  )
}
