import { useEffect, useState } from 'react'
import { api, formatTimecode, type Banner, type Candidate, type Segment } from '../api'
import { Button, Field, inputClass } from './ui'

/** Смещения границ одним нажатием — быстрее, чем вводить время руками. */
const NUDGES = [-3, -1, -0.5, +0.5, +1, +3]

export function ClipEditor({
  candidate,
  segments,
  banners,
  onClose,
  onSaved,
}: {
  candidate: Candidate
  segments: Segment[]
  banners: Banner[]
  onClose: () => void
  onSaved: () => void
}) {
  const [start, setStart] = useState(candidate.start)
  const [end, setEnd] = useState(candidate.end)
  const [title, setTitle] = useState(candidate.title ?? '')
  const [cropMode, setCropMode] = useState(candidate.crop_mode ?? '')
  const [subtitles, setSubtitles] = useState(
    candidate.subtitles_enabled == null ? '' : String(candidate.subtitles_enabled),
  )
  const [bannerId, setBannerId] = useState(
    candidate.banner_id == null ? '' : String(candidate.banner_id),
  )
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  const inside = segments.filter((s) => s.end > start && s.start < end)
  const duration = end - start

  const save = async (rerender: boolean) => {
    setBusy(true)
    setError(null)
    try {
      await api.updateCandidate(candidate.id, {
        start,
        end,
        title,
        crop_mode: cropMode || null,
        subtitles_enabled: subtitles === '' ? null : Number(subtitles),
        banner_id: bannerId === '' ? null : Number(bannerId),
      } as Partial<Candidate>)
      if (rerender) await api.renderFinal(candidate.id)
      onSaved()
      onClose()
    } catch (e) {
      setError(String((e as Error).message ?? e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div
      className="fixed inset-0 z-50 bg-black/70 backdrop-blur-sm flex items-start justify-center overflow-y-auto p-4"
      onClick={onClose}
    >
      <div
        className="w-full max-w-3xl rounded-xl border border-[var(--color-line)] bg-[var(--color-panel)] my-8"
        onClick={(e) => e.stopPropagation()}
      >
        <header className="flex items-center justify-between px-5 py-3 border-b border-[var(--color-line)]">
          <h2 className="text-sm font-medium">Редактирование шортса</h2>
          <Button variant="ghost" onClick={onClose}>
            Закрыть
          </Button>
        </header>

        <div className="p-5 space-y-5">
          <div className="grid sm:grid-cols-2 gap-4">
            <Field label="Название">
              <input className={inputClass} value={title} onChange={(e) => setTitle(e.target.value)} />
            </Field>
            <div className="flex items-end">
              <div className="text-sm text-neutral-400">
                {formatTimecode(start, true)} → {formatTimecode(end, true)} ·{' '}
                <span className={duration < 15 || duration > 95 ? 'text-[var(--color-warn)]' : ''}>
                  {duration.toFixed(1)} сек
                </span>
              </div>
            </div>
          </div>

          <div className="grid sm:grid-cols-2 gap-5">
            <div>
              <div className="text-xs text-neutral-400 mb-2">Начало</div>
              <div className="flex flex-wrap gap-1.5">
                {NUDGES.map((delta) => (
                  <Button
                    key={delta}
                    onClick={() => setStart((v) => Math.max(0, Math.round((v + delta) * 100) / 100))}
                  >
                    {delta > 0 ? `+${delta}` : delta}
                  </Button>
                ))}
              </div>
            </div>
            <div>
              <div className="text-xs text-neutral-400 mb-2">Конец</div>
              <div className="flex flex-wrap gap-1.5">
                {NUDGES.map((delta) => (
                  <Button
                    key={delta}
                    onClick={() => setEnd((v) => Math.round((v + delta) * 100) / 100)}
                  >
                    {delta > 0 ? `+${delta}` : delta}
                  </Button>
                ))}
              </div>
            </div>
          </div>

          <div>
            <div className="text-xs text-neutral-400 mb-2">
              Реплики фрагмента — нажмите, чтобы поставить границу по реплике
            </div>
            <div className="max-h-[220px] overflow-y-auto rounded-lg bg-[var(--color-panel-2)] p-2 space-y-0.5">
              {inside.map((segment) => (
                <div key={segment.id} className="flex items-center gap-2 text-xs">
                  <button
                    className="px-1.5 py-0.5 rounded hover:bg-white/10 text-neutral-500 shrink-0"
                    title="Начать отсюда"
                    onClick={() => setStart(Math.max(0, segment.start - 0.3))}
                  >
                    ⇤
                  </button>
                  <button
                    className="px-1.5 py-0.5 rounded hover:bg-white/10 text-neutral-500 shrink-0"
                    title="Закончить здесь"
                    onClick={() => setEnd(segment.end + 0.5)}
                  >
                    ⇥
                  </button>
                  <span className="text-neutral-500 tabular-nums shrink-0">
                    {formatTimecode(segment.start)}
                  </span>
                  <span className="text-neutral-300 truncate">{segment.text}</span>
                </div>
              ))}
              {inside.length === 0 && (
                <p className="text-xs text-neutral-500 p-2">В выбранных границах реплик нет.</p>
              )}
            </div>
          </div>

          <div className="grid sm:grid-cols-3 gap-4">
            <Field label="Кадрирование" hint="Пусто — как в общих настройках">
              <select className={inputClass} value={cropMode} onChange={(e) => setCropMode(e.target.value)}>
                <option value="">По умолчанию</option>
                <option value="smart">Следить за лицами</option>
                <option value="blur">Размытый фон</option>
                <option value="static">По центру</option>
              </select>
            </Field>

            <Field label="Субтитры">
              <select className={inputClass} value={subtitles} onChange={(e) => setSubtitles(e.target.value)}>
                <option value="">По умолчанию</option>
                <option value="1">Включить</option>
                <option value="0">Выключить</option>
              </select>
            </Field>

            <Field label="Баннер">
              <select className={inputClass} value={bannerId} onChange={(e) => setBannerId(e.target.value)}>
                <option value="">По умолчанию</option>
                {banners.map((b) => (
                  <option key={b.id} value={b.id}>
                    {b.title}
                  </option>
                ))}
              </select>
            </Field>
          </div>

          {error && <p className="text-xs text-[var(--color-bad)]">{error}</p>}

          <div className="flex flex-wrap gap-2 justify-end">
            <Button onClick={onClose}>Отмена</Button>
            <Button variant="default" onClick={() => save(false)} disabled={busy}>
              Сохранить
            </Button>
            <Button variant="primary" onClick={() => save(true)} disabled={busy}>
              Сохранить и отрендерить
            </Button>
          </div>
        </div>
      </div>
    </div>
  )
}
