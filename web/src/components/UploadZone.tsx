import { useRef, useState } from 'react'
import { formatBytes, uploadVideo, type UploadHandle } from '../api'
import { Button } from './ui'

const ACCEPT = '.mp4,.mkv,.mov,.avi,.webm'

export function UploadZone({
  maxBytes,
  onUploaded,
}: {
  maxBytes: number
  onUploaded: (videoId: number) => void
}) {
  const inputRef = useRef<HTMLInputElement>(null)
  const handleRef = useRef<UploadHandle>({ cancel: () => {} })
  const [dragging, setDragging] = useState(false)
  const [busy, setBusy] = useState(false)
  const [stage, setStage] = useState('')
  const [sent, setSent] = useState(0)
  const [total, setTotal] = useState(0)
  const [error, setError] = useState<string | null>(null)

  const start = async (file: File) => {
    setError(null)
    if (file.size > maxBytes) {
      setError(
        `Файл ${formatBytes(file.size)} не поместится: свободно ${formatBytes(maxBytes)}. ` +
          'Удалите предыдущую серию и её шортсы.',
      )
      return
    }
    setBusy(true)
    setSent(0)
    setTotal(file.size)
    try {
      const res = await uploadVideo(file, (s, t) => (setSent(s), setTotal(t)), setStage, handleRef.current)
      setStage('')
      onUploaded(res.video_id)
    } catch (e) {
      setError(String((e as Error).message ?? e))
    } finally {
      setBusy(false)
    }
  }

  const percent = total ? Math.round((sent / total) * 100) : 0

  return (
    <div>
      <div
        onDragOver={(e) => (e.preventDefault(), setDragging(true))}
        onDragLeave={() => setDragging(false)}
        onDrop={(e) => {
          e.preventDefault()
          setDragging(false)
          const file = e.dataTransfer.files?.[0]
          if (file && !busy) start(file)
        }}
        onClick={() => !busy && inputRef.current?.click()}
        className={`rounded-xl border-2 border-dashed px-6 py-10 text-center transition-colors cursor-pointer ${
          dragging
            ? 'border-[var(--color-accent)] bg-[var(--color-accent)]/5'
            : 'border-[var(--color-line)] hover:border-neutral-600'
        } ${busy ? 'pointer-events-none opacity-70' : ''}`}
      >
        <input
          ref={inputRef}
          type="file"
          accept={ACCEPT}
          className="hidden"
          onChange={(e) => {
            const file = e.target.files?.[0]
            if (file) start(file)
            e.target.value = ''
          }}
        />
        {!busy ? (
          <>
            <p className="text-sm text-neutral-200">Перетащите серию сюда или нажмите, чтобы выбрать</p>
            <p className="text-xs text-neutral-500 mt-2">
              MP4, MKV, MOV, AVI, WEBM · максимум {formatBytes(maxBytes)}
            </p>
          </>
        ) : (
          <div className="space-y-3">
            <p className="text-sm text-neutral-200">{stage}</p>
            <div className="h-2 rounded-full bg-[var(--color-panel-2)] overflow-hidden">
              <div
                className="h-full bg-[var(--color-accent)] transition-[width] duration-300"
                style={{ width: `${percent}%` }}
              />
            </div>
            <p className="text-xs text-neutral-400">
              {formatBytes(sent)} из {formatBytes(total)} · {percent}%
            </p>
          </div>
        )}
      </div>

      {busy && (
        <div className="mt-3 flex justify-center">
          <Button variant="ghost" onClick={() => handleRef.current.cancel()}>
            Отменить загрузку
          </Button>
        </div>
      )}

      {error && (
        <div className="mt-3 rounded-lg border border-[var(--color-bad)]/40 bg-[var(--color-bad)]/10 px-4 py-3 text-sm text-[#ffb4b8]">
          {error}
        </div>
      )}
    </div>
  )
}
