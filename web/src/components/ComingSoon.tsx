import { EmptyState } from './EmptyState'

/** A tab whose phase has not landed yet. Removed as each one is built. */
export function ComingSoon({ what }: { what: string }) {
  return (
    <EmptyState title={`${what} is not built yet`}>
      This part of the interface lands in a later phase. Nothing is missing from the engine — the
      data is there, the screen is not.
    </EmptyState>
  )
}
