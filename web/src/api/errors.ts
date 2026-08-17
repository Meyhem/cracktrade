/**
 * One error type for two wire formats.
 *
 * The API speaks `application/problem+json` for domain errors it raises itself, and FastAPI's
 * own `{detail: [...]}` shape for request-body validation it never reaches. A client that
 * handled only the first would render "[object Object]" the first time someone posted a
 * malformed body, so both are normalised here and nowhere else.
 */

/** One field-level complaint, from either wire format. */
export type Issue = {
  path: string
  message: string
  line?: number | null
  suggestion?: string | null
}

/**
 * What went wrong, in the terms the UI branches on.
 *
 * `kind` is derived from the problem `type` slug rather than from the status code, because
 * 422 arrives from both formats and means different things in each.
 */
export type ApiErrorKind =
  'not_found' | 'conflict' | 'config_invalid' | 'invariant' | 'request_invalid' | 'network' | 'http'

export class ApiError extends Error {
  readonly status: number
  readonly kind: ApiErrorKind
  readonly title: string
  readonly detail: string
  /** Echoed by every response; the one thing worth quoting in a bug report. */
  readonly requestId: string | null
  readonly issues: Issue[]

  constructor(init: {
    status: number
    kind: ApiErrorKind
    title: string
    detail: string
    requestId?: string | null
    issues?: Issue[]
  }) {
    super(init.detail || init.title)
    this.name = 'ApiError'
    this.status = init.status
    this.kind = init.kind
    this.title = init.title
    this.detail = init.detail
    this.requestId = init.requestId ?? null
    this.issues = init.issues ?? []
  }
}

const KIND_BY_SLUG: Record<string, ApiErrorKind> = {
  'not-found': 'not_found',
  conflict: 'conflict',
  'config-invalid': 'config_invalid',
  'invariant-violation': 'invariant',
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function asString(value: unknown, fallback: string): string {
  return typeof value === 'string' ? value : fallback
}

/** Map a problem `type` URI onto the kind the UI branches on. */
function kindFromType(type: unknown, status: number): ApiErrorKind {
  if (typeof type === 'string') {
    const slug = type.split('/').pop() ?? ''
    const kind = KIND_BY_SLUG[slug]
    if (kind) return kind
  }
  if (status === 404) return 'not_found'
  if (status === 409) return 'conflict'
  return 'http'
}

/** `errors: [{path, message, line, suggestion}]` on a config-invalid problem. */
function issuesFromProblem(raw: unknown): Issue[] {
  if (!Array.isArray(raw)) return []
  return raw.filter(isRecord).map((item) => ({
    path: asString(item.path, ''),
    message: asString(item.message, ''),
    line: typeof item.line === 'number' ? item.line : null,
    suggestion: typeof item.suggestion === 'string' ? item.suggestion : null,
  }))
}

/**
 * FastAPI's `detail: [{loc, msg, type}]`. `loc` starts with "body"/"query", which is noise to
 * a user, so it is dropped and the rest joined into a path the form can match against.
 */
function issuesFromFastApi(raw: unknown[]): Issue[] {
  return raw.filter(isRecord).map((item) => {
    const loc = Array.isArray(item.loc) ? item.loc : []
    const path = loc
      .filter((part) => part !== 'body' && part !== 'query' && part !== 'path')
      .join('.')
    return { path, message: asString(item.msg, 'invalid value'), line: null, suggestion: null }
  })
}

/**
 * Build an `ApiError` from a non-2xx response. The body is read here rather than by callers,
 * because a response body can only be consumed once and every caller wants the same thing.
 */
export async function toApiError(response: Response): Promise<ApiError> {
  const requestId = response.headers.get('x-request-id')
  let body: unknown = null
  try {
    body = await response.json()
  } catch {
    // A plain-text 500 from an unhandled server-side exception lands here; the status and
    // the request id are still worth surfacing, so this is not fatal.
  }

  if (isRecord(body) && typeof body.type === 'string' && typeof body.title === 'string') {
    return new ApiError({
      status: response.status,
      kind: kindFromType(body.type, response.status),
      title: body.title,
      detail: asString(body.detail, body.title),
      requestId: asString(body.request_id, requestId ?? '') || requestId,
      issues: issuesFromProblem(body.errors),
    })
  }

  if (isRecord(body) && Array.isArray(body.detail)) {
    const issues = issuesFromFastApi(body.detail)
    return new ApiError({
      status: response.status,
      kind: 'request_invalid',
      title: 'The request was rejected',
      detail:
        issues.length > 0
          ? issues.map((issue) => `${issue.path}: ${issue.message}`).join('; ')
          : 'The request body did not match what this endpoint accepts.',
      requestId,
      issues,
    })
  }

  return new ApiError({
    status: response.status,
    kind: 'http',
    title: `HTTP ${response.status}`,
    detail:
      isRecord(body) && typeof body.detail === 'string'
        ? body.detail
        : `The server returned ${response.status} ${response.statusText}.`,
    requestId,
  })
}

/** The fetch itself failed: no server, or the browser cut the connection. */
export function networkError(cause: unknown): ApiError {
  return new ApiError({
    status: 0,
    kind: 'network',
    title: 'Could not reach the API',
    detail:
      cause instanceof Error
        ? `${cause.message}. Is \`cracktrade-api serve\` running on port 8000?`
        : 'Is `cracktrade-api serve` running on port 8000?',
  })
}
