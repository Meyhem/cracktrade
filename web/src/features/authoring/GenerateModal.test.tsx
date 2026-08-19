import { describe, expect, it, vi } from 'vitest'
import { http, HttpResponse } from 'msw'
import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { GenerateModal } from './GenerateModal'
import { renderWithProviders } from '../../test/render'
import { problem, server } from '../../test/server'

/**
 * The dialog that writes a strategy.
 *
 * These are mostly about restraint. The interesting failures here are not "the draft did not
 * appear" but "the draft appeared and the screen implied something about it that the engine
 * never said": that it is good, that it is saved, that it can be adopted when the validator
 * rejected it. Each of those gets its own test.
 */

const GENERATE_URL = '*/api/v1/config/generate'

const YAML = 'strategy:\n  name: written_by_a_machine\n'

function proposal(
  overrides: {
    valid?: boolean
    attempts?: number
    notes?: string
    errors?: { path: string; message: string }[]
    warnings?: { path: string; message: string }[]
  } = {},
) {
  const valid = overrides.valid ?? true
  return {
    yaml: YAML,
    notes: overrides.notes ?? 'A trend filter with a trailing stop. You did not name a ticker.',
    attempts: overrides.attempts ?? 1,
    review: {
      valid,
      errors: overrides.errors ?? [],
      warnings: overrides.warnings ?? [],
      canonical_yaml: valid ? YAML : null,
      config: null,
      namespace: ['close'],
      searchable_parameters: [],
    },
  }
}

function open(props: Partial<Parameters<typeof GenerateModal>[0]> = {}) {
  const onAdopt = vi.fn()
  const onClose = vi.fn()
  const rendered = renderWithProviders(
    <GenerateModal
      adoptLabel="Create strategy"
      allowInvalid={false}
      onAdopt={onAdopt}
      onClose={onClose}
      opened
      placeholder="Buy the dip"
      title="Write a strategy"
      {...props}
    />,
  )
  return { ...rendered, onAdopt, onClose }
}

async function write(text = 'buy NVDA pullbacks') {
  const user = userEvent.setup()
  await user.type(screen.getByRole('textbox', { name: /what should the strategy do/i }), text)
  await user.click(screen.getByRole('button', { name: 'Write it' }))
  return user
}

describe('before anything has been asked for', () => {
  it('will not generate an empty prompt', () => {
    open()
    expect(screen.getByRole('button', { name: 'Write it' })).toBeDisabled()
  })

  it('will not adopt a draft that does not exist', () => {
    open()
    expect(screen.getByRole('button', { name: 'Create strategy' })).toBeDisabled()
  })
})

describe('once a draft comes back', () => {
  it('shows the file, the notes, and the engine, not the app, saying it is valid', async () => {
    server.use(http.post(GENERATE_URL, () => HttpResponse.json(proposal())))
    open()
    await write()

    await waitFor(() => {
      expect(screen.getByText(/the engine accepts this file/)).toBeInTheDocument()
    })
    expect(screen.getByText(/written_by_a_machine/)).toBeInTheDocument()
    expect(screen.getByText(/You did not name a ticker/)).toBeInTheDocument()
  })

  it('says nothing has been created, because nothing has', async () => {
    server.use(http.post(GENERATE_URL, () => HttpResponse.json(proposal())))
    open()
    await write()

    await waitFor(() => {
      expect(screen.getByText(/Nothing has been created/)).toBeInTheDocument()
    })
  })

  it('reports what the draft cost, rather than presenting a fourth try as a first', async () => {
    server.use(http.post(GENERATE_URL, () => HttpResponse.json(proposal({ attempts: 3 }))))
    open()
    await write()

    await waitFor(() => {
      expect(screen.getByText(/rewritten 2 times against the validator/)).toBeInTheDocument()
    })
  })

  it('hands the exact YAML to the caller and nothing else', async () => {
    server.use(http.post(GENERATE_URL, () => HttpResponse.json(proposal())))
    const { onAdopt } = open()
    const user = await write()

    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'Create strategy' })).toBeEnabled()
    })
    await user.click(screen.getByRole('button', { name: 'Create strategy' }))

    expect(onAdopt).toHaveBeenCalledWith(YAML)
  })
})

describe('when the engine rejects the draft', () => {
  const rejected = proposal({
    valid: false,
    errors: [{ path: 'entry.signal', message: "references an unknown name 'nothing_declared'" }],
  })

  it('still shows it, because a near miss the user can fix is the useful part', async () => {
    server.use(http.post(GENERATE_URL, () => HttpResponse.json(rejected)))
    open()
    await write()

    await waitFor(() => {
      expect(screen.getByText(/the engine rejects this file/)).toBeInTheDocument()
    })
    expect(screen.getByText(/nothing_declared/)).toBeInTheDocument()
    expect(screen.getByText(/written_by_a_machine/)).toBeInTheDocument()
  })

  it('refuses to adopt it where adopting would be refused anyway', async () => {
    server.use(http.post(GENERATE_URL, () => HttpResponse.json(rejected)))
    open({ allowInvalid: false })
    await write()

    await waitFor(() => {
      expect(screen.getByText(/the engine rejects this file/)).toBeInTheDocument()
    })
    expect(screen.getByRole('button', { name: 'Create strategy' })).toBeDisabled()
  })

  it('allows it into an editor draft, where the user can finish it', async () => {
    server.use(http.post(GENERATE_URL, () => HttpResponse.json(rejected)))
    const { onAdopt } = open({ allowInvalid: true, adoptLabel: 'Load into the editor' })
    const user = await write()

    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'Load into the editor' })).toBeEnabled()
    })
    await user.click(screen.getByRole('button', { name: 'Load into the editor' }))

    expect(onAdopt).toHaveBeenCalledWith(YAML)
  })
})

describe('refining a draft', () => {
  it('sends the draft back with the note, not the original request', async () => {
    const bodies: unknown[] = []
    server.use(
      http.post(GENERATE_URL, async ({ request }) => {
        bodies.push(await request.json())
        return HttpResponse.json(proposal())
      }),
    )
    open()
    const user = await write('buy NVDA pullbacks')

    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'Regenerate' })).toBeInTheDocument()
    })
    await user.type(screen.getByRole('textbox', { name: /not quite right/i }), 'use ATR stops')
    await user.click(screen.getByRole('button', { name: 'Regenerate' }))

    await waitFor(() => expect(bodies).toHaveLength(2))
    expect(bodies[0]).toEqual({ instruction: 'buy NVDA pullbacks' })
    expect(bodies[1]).toEqual({ instruction: 'use ATR stops', base_yaml: YAML })
  })
})

describe('when the writer cannot be reached', () => {
  it('shows what the tool said, which is the only actionable version of it', async () => {
    server.use(
      http.post(GENERATE_URL, () =>
        problem({
          status: 503,
          slug: 'upstream-unavailable',
          title: 'Service unavailable',
          detail: 'the strategy writer could not be reached: OAuth session expired',
        }),
      ),
    )
    open()
    await write()

    await waitFor(() => {
      expect(screen.getByText(/OAuth session expired/)).toBeInTheDocument()
    })
    expect(screen.getByRole('button', { name: 'Create strategy' })).toBeDisabled()
  })
})

describe('revising an existing strategy', () => {
  it('sends the current file with the very first request', async () => {
    const bodies: unknown[] = []
    server.use(
      http.post(GENERATE_URL, async ({ request }) => {
        bodies.push(await request.json())
        return HttpResponse.json(proposal())
      }),
    )
    const user = userEvent.setup()
    open({ baseYaml: 'strategy:\n  name: existing\n', allowInvalid: true })

    await user.type(screen.getByRole('textbox', { name: /what should change/i }), 'add a stop')
    await user.click(screen.getByRole('button', { name: 'Write it' }))

    await waitFor(() => expect(bodies).toHaveLength(1))
    expect(bodies[0]).toEqual({
      instruction: 'add a stop',
      base_yaml: 'strategy:\n  name: existing\n',
    })
  })
})
