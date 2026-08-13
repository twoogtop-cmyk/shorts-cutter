import { useEffect, useState } from 'react'
import { api, type Settings } from '../api'
import { Button, ErrorBox, Field, Panel, inputClass } from '../components/ui'

const NUMBER_FIELDS: { key: string; label: string; hint?: string }[] = [
  { key: 'min_duration', label: 'Минимальная длительность, сек' },
  { key: 'target_min_duration', label: 'Желаемая длительность от, сек' },
  { key: 'target_max_duration', label: 'Желаемая длительность до, сек' },
  { key: 'max_duration', label: 'Максимальная длительность, сек' },
  { key: 'min_score', label: 'Минимальный рейтинг AI', hint: 'Моменты с оценкой ниже не создаются' },
  { key: 'pad_start', label: 'Запас в начале, сек' },
  { key: 'pad_end', label: 'Запас в конце, сек' },
]

export function SettingsPage() {
  const [values, setValues] = useState<Settings | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [saved, setSaved] = useState(false)
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    api.getSettings().then(setValues).catch((e) => setError(String(e.message ?? e)))
  }, [])

  const set = (key: string, value: string) => {
    setValues((prev) => (prev ? { ...prev, [key]: value } : prev))
    setSaved(false)
  }

  const save = () => {
    if (!values) return
    setSaving(true)
    api
      .saveSettings(values)
      .then((v) => {
        setValues(v)
        setSaved(true)
        setError(null)
      })
      .catch((e) => setError(String(e.message ?? e)))
      .finally(() => setSaving(false))
  }

  if (!values) {
    return (
      <div className="space-y-4">
        <ErrorBox error={error} />
        {!error && <p className="text-sm text-neutral-400">Загрузка настроек…</p>}
      </div>
    )
  }

  return (
    <div className="space-y-5">
      <ErrorBox error={error} />

      <Panel
        title="Параметры нарезки"
        actions={
          <div className="flex items-center gap-3">
            {saved && <span className="text-xs text-[var(--color-ok)]">Сохранено</span>}
            <Button variant="primary" onClick={save} disabled={saving}>
              {saving ? 'Сохраняем…' : 'Сохранить'}
            </Button>
          </div>
        }
      >
        <div className="grid sm:grid-cols-2 lg:grid-cols-3 gap-4">
          <Field label="Язык контента">
            <select className={inputClass} value={values.language} onChange={(e) => set('language', e.target.value)}>
              <option value="ru">Русский</option>
              <option value="en">Английский</option>
              <option value="auto">Определять автоматически</option>
            </select>
          </Field>

          {NUMBER_FIELDS.map((f) => (
            <Field key={f.key} label={f.label} hint={f.hint}>
              <input
                className={inputClass}
                type="number"
                step="0.1"
                value={values[f.key] ?? ''}
                onChange={(e) => set(f.key, e.target.value)}
              />
            </Field>
          ))}

          <Field label="Количество шортсов с серии">
            <select className={inputClass} value={values.max_shorts} onChange={(e) => set('max_shorts', e.target.value)}>
              <option value="auto">Автоматически</option>
              <option value="5">Не более 5</option>
              <option value="10">Не более 10</option>
              <option value="20">Не более 20</option>
            </select>
          </Field>
        </div>
      </Panel>

      <Panel title="Формат и качество">
        <div className="grid sm:grid-cols-2 lg:grid-cols-3 gap-4">
          <Field label="Режим кадрирования 16:9 → 9:16">
            <select className={inputClass} value={values.crop_mode} onChange={(e) => set('crop_mode', e.target.value)}>
              <option value="smart">Smart Crop (следить за лицами)</option>
              <option value="blur">Размытый фон</option>
              <option value="static">Центральное кадрирование</option>
            </select>
          </Field>

          <Field label="Профиль качества">
            <select
              className={inputClass}
              value={values.quality_profile}
              onChange={(e) => set('quality_profile', e.target.value)}
            >
              <option value="high">Высокое (1080×1920, CRF 18)</option>
              <option value="max">Максимальное (1080×1920, CRF 16)</option>
            </select>
          </Field>

          <Field label="Модель для анализа">
            <select className={inputClass} value={values.llm_model} onChange={(e) => set('llm_model', e.target.value)}>
              <option value="claude-sonnet-4-5">claude-sonnet-4-5</option>
              <option value="claude-haiku-4-5">claude-haiku-4-5 (дешевле)</option>
              <option value="claude-opus-4-5">claude-opus-4-5 (дороже)</option>
            </select>
          </Field>

          <Field label="Субтитры">
            <select
              className={inputClass}
              value={values.subtitles_enabled}
              onChange={(e) => set('subtitles_enabled', e.target.value)}
            >
              <option value="1">Включены</option>
              <option value="0">Выключены</option>
            </select>
          </Field>

          <Field label="Стиль субтитров">
            <select
              className={inputClass}
              value={values.subtitle_style}
              onChange={(e) => set('subtitle_style', e.target.value)}
            >
              <option value="dynamic">Dynamic — подсветка слова</option>
              <option value="classic">Classic</option>
              <option value="minimal">Minimal</option>
            </select>
          </Field>
        </div>
      </Panel>

      <Panel title="Финальная плашка">
        <p className="text-xs text-neutral-500 mb-4">
          Компактный текст по центру кадра в последние секунды шортса — например призыв
          подписаться. Показывается поверх видео, не перекрывая субтитры.
        </p>
        <div className="grid sm:grid-cols-2 lg:grid-cols-4 gap-4">
          <Field label="Показывать">
            <select
              className={inputClass}
              value={values.outro_enabled}
              onChange={(e) => set('outro_enabled', e.target.value)}
            >
              <option value="0">Выключено</option>
              <option value="1">Включено</option>
            </select>
          </Field>

          <Field label="Длительность, сек">
            <input
              className={inputClass}
              type="number"
              min={1}
              max={10}
              step="0.5"
              value={values.outro_duration}
              onChange={(e) => set('outro_duration', e.target.value)}
            />
          </Field>

          <Field label="Размер шрифта">
            <input
              className={inputClass}
              type="number"
              min={24}
              max={140}
              value={values.outro_font_size}
              onChange={(e) => set('outro_font_size', e.target.value)}
            />
          </Field>

          <Field
            label="Положение"
            hint="Авто — плашка встаёт туда, где не закроет лицо"
          >
            <select
              className={inputClass}
              value={values.outro_position}
              onChange={(e) => set('outro_position', e.target.value)}
            >
              <option value="auto">Авто — мимо лиц</option>
              <option value="center">По центру</option>
              <option value="bottom">Внизу, над субтитрами</option>
              <option value="top">Вверху</option>
            </select>
          </Field>

          <Field label="Затемнение подложки, %" hint="0 — без подложки">
            <input
              className={inputClass}
              type="number"
              min={0}
              max={100}
              value={values.outro_bg_opacity}
              onChange={(e) => set('outro_bg_opacity', e.target.value)}
            />
          </Field>

          <div className="sm:col-span-2 lg:col-span-4">
            <Field label="Текст" hint="Перенос строки — новая строка на видео">
              <textarea
                className={`${inputClass} min-h-[80px] resize-y`}
                value={values.outro_text}
                placeholder={'Подпишись\nчтобы не пропустить продолжение'}
                onChange={(e) => set('outro_text', e.target.value)}
              />
            </Field>
          </div>
        </div>
      </Panel>
    </div>
  )
}
