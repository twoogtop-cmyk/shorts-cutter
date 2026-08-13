import { useEffect, useRef, useState } from 'react'
import { api, type Banner, type Settings } from '../api'
import { Button, ErrorBox, Field, Panel, inputClass } from '../components/ui'

/** Как баннер ляжет на ролик — видно до рендера. */
function BannerPreview({
  banner,
  mode,
  heightPercent,
  opacity,
}: {
  banner: Banner | null
  mode: string
  heightPercent: number
  opacity: number
}) {
  const areaHeight = `${heightPercent}%`
  return (
    <div className="relative w-[160px] aspect-[9/16] rounded-lg overflow-hidden border border-[var(--color-line)] bg-[#11141c] shrink-0">
      <div
        className="absolute left-0 right-0 bg-[#20242f] flex items-center justify-center text-[10px] text-neutral-500"
        style={{
          top: mode === 'separate_top' ? areaHeight : 0,
          bottom: 0,
        }}
      >
        видео
      </div>
      {banner ? (
        <img
          src={banner.url}
          alt=""
          className="absolute top-0 left-0 w-full object-contain"
          style={{ height: mode === 'separate_top' ? areaHeight : 'auto', opacity: opacity / 100 }}
        />
      ) : (
        <div
          className="absolute top-0 left-0 w-full bg-[var(--color-accent)]/25 flex items-center justify-center text-[10px] text-neutral-300"
          style={{ height: areaHeight }}
        >
          баннер
        </div>
      )}
      <div className="absolute left-0 right-0 bottom-[22%] h-[8%] bg-white/10 flex items-center justify-center text-[9px] text-neutral-400">
        субтитры
      </div>
      <div className="absolute left-0 right-0 bottom-0 h-[15%] border-t border-dashed border-white/20 flex items-end justify-center pb-1 text-[9px] text-neutral-500">
        зона YouTube
      </div>
    </div>
  )
}

export function BannersPage() {
  const [banners, setBanners] = useState<Banner[]>([])
  const [settings, setSettings] = useState<Settings | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [saved, setSaved] = useState(false)
  const inputRef = useRef<HTMLInputElement>(null)

  const reload = () => {
    api.listBanners().then(setBanners).catch((e) => setError(String(e.message ?? e)))
  }

  useEffect(() => {
    reload()
    api.getSettings().then(setSettings).catch((e) => setError(String(e.message ?? e)))
  }, [])

  const upload = async (file: File) => {
    setBusy(true)
    setError(null)
    try {
      await api.uploadBanner(file)
      reload()
    } catch (e) {
      setError(String((e as Error).message ?? e))
    } finally {
      setBusy(false)
    }
  }

  const remove = async (banner: Banner) => {
    if (!confirm(`Удалить баннер «${banner.title}»?`)) return
    try {
      await api.deleteBanner(banner.id)
      reload()
      if (settings?.banner_id === String(banner.id)) {
        setSettings({ ...settings, banner_id: '' })
      }
    } catch (e) {
      setError(String((e as Error).message ?? e))
    }
  }

  const set = (key: string, value: string) => {
    setSettings((prev) => (prev ? { ...prev, [key]: value } : prev))
    setSaved(false)
  }

  const save = () => {
    if (!settings) return
    setBusy(true)
    api
      .saveSettings(settings)
      .then((v) => {
        setSettings(v)
        setSaved(true)
      })
      .catch((e) => setError(String(e.message ?? e)))
      .finally(() => setBusy(false))
  }

  const activeId = settings?.banner_id ?? ''
  const active = banners.find((b) => String(b.id) === activeId) ?? null

  return (
    <div className="space-y-5">
      <ErrorBox error={error} />

      <Panel
        title="Мои баннеры"
        actions={
          <Button variant="primary" onClick={() => inputRef.current?.click()} disabled={busy}>
            {busy ? 'Загружаем…' : 'Добавить баннер'}
          </Button>
        }
      >
        <input
          ref={inputRef}
          type="file"
          accept=".png,.webp,.jpg,.jpeg"
          className="hidden"
          onChange={(e) => {
            const file = e.target.files?.[0]
            if (file) upload(file)
            e.target.value = ''
          }}
        />

        {banners.length === 0 ? (
          <p className="text-sm text-neutral-400">
            Баннеров пока нет. PNG и WEBP с прозрачностью поддерживаются — фон останется прозрачным.
          </p>
        ) : (
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
            {banners.map((banner) => {
              const isActive = String(banner.id) === activeId
              return (
                <div
                  key={banner.id}
                  className={`rounded-lg border p-3 space-y-2 transition-colors ${
                    isActive
                      ? 'border-[var(--color-accent)] bg-[var(--color-accent)]/5'
                      : 'border-[var(--color-line)]'
                  }`}
                >
                  <div className="rounded bg-[#0e1117] p-2 flex items-center justify-center h-[70px] bg-[repeating-conic-gradient(#1a1e28_0%_25%,#141821_0%_50%)] bg-[length:16px_16px]">
                    <img src={banner.url} alt={banner.title} className="max-h-full max-w-full object-contain" />
                  </div>
                  <div className="text-xs text-neutral-300 truncate">{banner.title}</div>
                  <div className="text-[11px] text-neutral-500">
                    {banner.width && banner.height ? `${banner.width}×${banner.height}` : '—'}
                  </div>
                  <div className="flex gap-1.5">
                    <Button
                      variant={isActive ? 'ok' : 'default'}
                      className="flex-1"
                      onClick={() => set('banner_id', isActive ? '' : String(banner.id))}
                    >
                      {isActive ? 'Выбран' : 'Выбрать'}
                    </Button>
                    <Button variant="danger" onClick={() => remove(banner)}>
                      ✕
                    </Button>
                  </div>
                </div>
              )
            })}
          </div>
        )}
      </Panel>

      {settings && (
        <Panel
          title="Размещение баннера"
          actions={
            <div className="flex items-center gap-3">
              {saved && <span className="text-xs text-[var(--color-ok)]">Сохранено</span>}
              <Button variant="primary" onClick={save} disabled={busy}>
                Сохранить
              </Button>
            </div>
          }
        >
          <div className="flex flex-col lg:flex-row gap-6">
            <BannerPreview
              banner={active}
              mode={settings.banner_mode}
              heightPercent={Number(settings.banner_height_percent) || 18}
              opacity={Number(settings.banner_opacity) || 100}
            />

            <div className="grid sm:grid-cols-2 gap-4 flex-1">
              <Field
                label="Режим"
                hint={
                  settings.banner_mode === 'separate_top'
                    ? 'Видео сдвигается вниз, баннер в своей полосе и ничего не перекрывает'
                    : 'Баннер лежит поверх видео'
                }
              >
                <select
                  className={inputClass}
                  value={settings.banner_mode}
                  onChange={(e) => set('banner_mode', e.target.value)}
                >
                  <option value="separate_top">Отдельная полоса сверху</option>
                  <option value="overlay">Поверх видео</option>
                </select>
              </Field>

              <Field label="Высота полосы, % от кадра">
                <input
                  className={inputClass}
                  type="number"
                  min={5}
                  max={40}
                  value={settings.banner_height_percent}
                  onChange={(e) => set('banner_height_percent', e.target.value)}
                />
              </Field>

              <Field label="Прозрачность, %">
                <input
                  className={inputClass}
                  type="number"
                  min={10}
                  max={100}
                  value={settings.banner_opacity}
                  onChange={(e) => set('banner_opacity', e.target.value)}
                />
              </Field>

              <Field label="Баннер по умолчанию">
                <select
                  className={inputClass}
                  value={activeId}
                  onChange={(e) => set('banner_id', e.target.value)}
                >
                  <option value="">Не использовать</option>
                  {banners.map((b) => (
                    <option key={b.id} value={b.id}>
                      {b.title}
                    </option>
                  ))}
                </select>
              </Field>
            </div>
          </div>
        </Panel>
      )}
    </div>
  )
}
