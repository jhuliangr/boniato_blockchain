import type { CSSProperties } from 'react'

/**
 * Builds an inline style object of CSS custom properties.
 * React accepts them at runtime but `CSSProperties` has no index signature,
 * so the cast lives here once instead of at every call site.
 */
export function cssVars(vars: Record<string, string | number>): CSSProperties {
  return vars as CSSProperties
}

/** Joins class names, skipping falsy entries. */
export function cx(...parts: (string | false | null | undefined)[]): string {
  return parts.filter(Boolean).join(' ')
}
