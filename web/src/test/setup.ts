import '@testing-library/jest-dom/vitest'
import { afterAll, afterEach, beforeAll, vi } from 'vitest'
import { server } from './server'

/**
 * ECharts renders to a canvas, and jsdom has none — `getContext('2d')` returns null, which
 * `echarts.init` cannot survive. Stubbed for every test file rather than per file.
 *
 * This is a deliberate limit on what these tests prove. They cover which chart is drawn,
 * which is refused, and what is said in its place; they say nothing about pixels. That
 * division is the right one: §5.2's suppression rules are the part that regresses into
 * silently rendering a figure, and that regression is invisible until it has misled someone.
 */
vi.mock('echarts', () => ({
  init: () => ({
    setOption: () => {},
    resize: () => {},
    dispose: () => {},
    getDataURL: () => 'data:image/png;base64,',
    group: '',
  }),
  connect: () => {},
}))

beforeAll(() => server.listen({ onUnhandledRequest: 'error' }))
afterEach(() => server.resetHandlers())
afterAll(() => server.close())

// jsdom implements neither, and Mantine reads both on mount.
Object.defineProperty(window, 'matchMedia', {
  writable: true,
  value: (query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addEventListener: () => {},
    removeEventListener: () => {},
    dispatchEvent: () => false,
  }),
})

// jsdom implements no font-loading API. Mantine's autosizing textarea subscribes to
// `document.fonts` on mount to re-measure once a webfont arrives, and an undefined property
// there throws during the effect rather than being tolerated.
Object.defineProperty(document, 'fonts', {
  writable: true,
  value: {
    addEventListener: () => {},
    removeEventListener: () => {},
  },
})

class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}

window.ResizeObserver = ResizeObserverStub as unknown as typeof ResizeObserver

// jsdom has no layout, so it implements no scrolling. Mantine's combobox calls this on a
// timer after the dropdown opens, which lands *after* the test that opened it has finished —
// an unhandled rejection that vitest rightly warns can turn into a false pass elsewhere.
Element.prototype.scrollIntoView = () => {}
