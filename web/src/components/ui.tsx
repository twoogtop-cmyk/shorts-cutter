import type { ReactNode } from 'react'

export function Panel({
  title,
  actions,
  children,
  className = '',
}: {
  title?: ReactNode
  actions?: ReactNode
  children: ReactNode
  className?: string
}) {
  return (
    <section
      className={`rounded-xl border border-[var(--color-line)] bg-[var(--color-panel)] ${className}`}
    >
      {(title || actions) && (
        <header className="flex items-center justify-between gap-4 px-5 py-3 border-b border-[var(--color-line)]">
          <h2 className="text-sm font-medium text-neutral-200">{title}</h2>
          {actions}
        </header>
      )}
      <div className="p-5">{children}</div>
    </section>
  )
}

export function Button({
  children,
  onClick,
  variant = 'default',
  disabled,
  type = 'button',
  className = '',
  title,
}: {
  children: ReactNode
  onClick?: () => void
  variant?: 'default' | 'primary' | 'ok' | 'danger' | 'ghost'
  disabled?: boolean
  type?: 'button' | 'submit'
  className?: string
  title?: string
}) {
  const styles: Record<string, string> = {
    default: 'bg-[var(--color-panel-2)] hover:bg-[#232a38] text-neutral-100 border-[var(--color-line)]',
    primary: 'bg-[var(--color-accent)] hover:bg-[#5d97ff] text-white border-transparent',
    ok: 'bg-[var(--color-ok)]/15 hover:bg-[var(--color-ok)]/25 text-[var(--color-ok)] border-[var(--color-ok)]/30',
    danger: 'bg-[var(--color-bad)]/15 hover:bg-[var(--color-bad)]/25 text-[var(--color-bad)] border-[var(--color-bad)]/30',
    ghost: 'bg-transparent hover:bg-[var(--color-panel-2)] text-neutral-300 border-transparent',
  }
  return (
    <button
      type={type}
      title={title}
      onClick={onClick}
      disabled={disabled}
      className={`px-3 py-1.5 rounded-lg border text-sm font-medium transition-colors disabled:opacity-40 disabled:cursor-not-allowed ${styles[variant]} ${className}`}
    >
      {children}
    </button>
  )
}

export function Field({ label, hint, children }: { label: string; hint?: string; children: ReactNode }) {
  return (
    <label className="block">
      <span className="block text-xs text-neutral-400 mb-1.5">{label}</span>
      {children}
      {hint && <span className="block text-xs text-neutral-500 mt-1">{hint}</span>}
    </label>
  )
}

export const inputClass =
  'w-full px-3 py-2 rounded-lg bg-[var(--color-panel-2)] border border-[var(--color-line)] text-sm text-neutral-100 outline-none focus:border-[var(--color-accent)] transition-colors'

export function Stat({ label, value, tone }: { label: string; value: ReactNode; tone?: 'ok' | 'warn' | 'bad' }) {
  const color =
    tone === 'ok' ? 'text-[var(--color-ok)]' : tone === 'warn' ? 'text-[var(--color-warn)]' : tone === 'bad' ? 'text-[var(--color-bad)]' : 'text-neutral-100'
  return (
    <div className="rounded-lg bg-[var(--color-panel-2)] px-4 py-3">
      <div className="text-xs text-neutral-400">{label}</div>
      <div className={`text-lg font-semibold mt-0.5 ${color}`}>{value}</div>
    </div>
  )
}

export function ErrorBox({ error }: { error: string | null }) {
  if (!error) return null
  return (
    <div className="rounded-lg border border-[var(--color-bad)]/40 bg-[var(--color-bad)]/10 px-4 py-3 text-sm text-[#ffb4b8]">
      {error}
    </div>
  )
}
