type ApiRequestMethod = 'GET' | 'POST' | 'PATCH' | 'DELETE'

interface ApiErrorOptions {
  method: ApiRequestMethod
  url: string
  status: number | null
  statusText: string | null
  body: unknown
  cause?: unknown
}

class ApiError extends Error {
  readonly method: ApiRequestMethod
  readonly url: string
  readonly status: number | null
  readonly statusText: string | null
  readonly body: unknown

  constructor(message: string, options: ApiErrorOptions) {
    super(message, { cause: options.cause })
    this.name = 'ApiError'
    this.method = options.method
    this.url = options.url
    this.status = options.status
    this.statusText = options.statusText
    this.body = options.body
  }
}

type ApiErrorListener = () => void

const listeners = new Set<ApiErrorListener>()
let currentApiError: ApiError | null = null

function reportApiError(error: ApiError): void {
  currentApiError = error
  listeners.forEach((listener) => listener())
}

function clearApiError(): void {
  currentApiError = null
  listeners.forEach((listener) => listener())
}

function subscribeToApiErrors(listener: ApiErrorListener): () => void {
  listeners.add(listener)
  return () => listeners.delete(listener)
}

function getCurrentApiError(): ApiError | null {
  return currentApiError
}

export { ApiError, clearApiError, getCurrentApiError, reportApiError, subscribeToApiErrors }
export type { ApiRequestMethod }
