import { describe, expect, it } from 'vitest'
import { toApiError } from './errors'

function respond(body: unknown, init: { status: number; contentType?: string }): Response {
  return new Response(JSON.stringify(body), {
    status: init.status,
    headers: {
      'content-type': init.contentType ?? 'application/json',
      'x-request-id': 'req-abc',
    },
  })
}

describe('toApiError', () => {
  it('reads a problem+json conflict, keeping the server prose', async () => {
    const error = await toApiError(
      respond(
        {
          type: 'https://cracktrade.invalid/errors/conflict',
          title: 'Conflict',
          status: 409,
          detail: 'this edit was based on v7, but the head is now v8.',
          request_id: 'req-abc',
        },
        { status: 409, contentType: 'application/problem+json' },
      ),
    )

    expect(error.kind).toBe('conflict')
    expect(error.detail).toContain('head is now v8')
    expect(error.requestId).toBe('req-abc')
  })

  it('carries config-invalid issues through with their line numbers', async () => {
    const error = await toApiError(
      respond(
        {
          type: 'https://cracktrade.invalid/errors/config-invalid',
          title: 'Configuration is not valid',
          status: 422,
          detail: 'the configuration has 1 error',
          errors: [
            {
              path: 'entry.signal',
              message: "'rsi_indd' is not defined",
              line: 12,
              suggestion: '(close > sma) & (rsi < 30)',
            },
          ],
        },
        { status: 422, contentType: 'application/problem+json' },
      ),
    )

    expect(error.kind).toBe('config_invalid')
    expect(error.issues).toEqual([
      {
        path: 'entry.signal',
        message: "'rsi_indd' is not defined",
        line: 12,
        suggestion: '(close > sma) & (rsi < 30)',
      },
    ])
  })

  it("normalises FastAPI's own 422, dropping the 'body' prefix from loc", async () => {
    const error = await toApiError(
      respond(
        {
          detail: [
            { loc: ['body', 'base_version'], msg: 'Field required', type: 'missing' },
            { loc: ['body', 'params', 'epochs'], msg: 'Input should be <= 200', type: 'less_than' },
          ],
        },
        { status: 422 },
      ),
    )

    expect(error.kind).toBe('request_invalid')
    expect(error.issues.map((issue) => issue.path)).toEqual(['base_version', 'params.epochs'])
    expect(error.detail).toContain('Field required')
  })

  it('survives a plain-text 500 with no JSON body at all', async () => {
    const response = new Response('Internal Server Error', {
      status: 500,
      headers: { 'content-type': 'text/plain', 'x-request-id': 'req-abc' },
    })

    const error = await toApiError(response)

    expect(error.kind).toBe('http')
    expect(error.status).toBe(500)
    expect(error.requestId).toBe('req-abc')
    expect(error.detail).toContain('500')
  })

  it('falls back to the status code when the problem type slug is unknown', async () => {
    const error = await toApiError(
      respond(
        {
          type: 'https://cracktrade.invalid/errors/something-new',
          title: 'Not found',
          status: 404,
          detail: 'no such strategy',
        },
        { status: 404, contentType: 'application/problem+json' },
      ),
    )

    expect(error.kind).toBe('not_found')
  })
})
