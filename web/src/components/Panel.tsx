import type { ReactNode } from 'react'

interface PanelProps {
  title: string
  aside?: ReactNode
  /** Shown as a strip under the header — a load failure that is not fatal. */
  error?: string | null
  /** Dims the panel while its data is known to be out of date. */
  stale?: boolean
  accent?: boolean
  flush?: boolean
  bodyClassName?: string
  children: ReactNode
}

/** The one container used by every section, so headers and spacing agree. */
export function Panel({
  title,
  aside,
  error,
  stale = false,
  accent = false,
  flush = false,
  bodyClassName,
  children,
}: PanelProps) {
  const classes = ['panel']
  if (accent) classes.push('panel--accent')
  if (stale) classes.push('is-stale')

  const bodyClasses = ['panel__body']
  if (flush) bodyClasses.push('panel__body--flush')
  if (bodyClassName) bodyClasses.push(bodyClassName)

  return (
    <section className={classes.join(' ')}>
      <header className="panel__head">
        <h2 className="panel__title">{title}</h2>
        {aside ? <div className="panel__aside">{aside}</div> : null}
      </header>
      {error ? <p className="panel__error">{error}</p> : null}
      <div className={bodyClasses.join(' ')}>{children}</div>
    </section>
  )
}

/** A key/value cell used inside panels. */
export function Fact({
  label,
  value,
  title,
  strong = false,
}: {
  label: string
  value: ReactNode
  title?: string
  strong?: boolean
}) {
  return (
    <div className="fact" title={title}>
      <span className="fact__key">{label}</span>
      <span className={`fact__value${strong ? ' fact__value--strong' : ''} truncate`}>{value}</span>
    </div>
  )
}
