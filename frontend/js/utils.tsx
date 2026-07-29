import Cookies from 'js-cookie'

import { ApiError, reportApiError, type ApiRequestMethod } from './api/errors'

const LOGIN_PATH = '/accounts/login/'
const NOT_AUTHENTICATED_DETAIL = 'Authentication credentials were not provided.'

function responseStatus(response: Response): string {
  return `HTTP ${response.status}${response.statusText ? ` ${response.statusText}` : ''}`
}

function isJsonResponse(response: Response): boolean {
  const contentType = response.headers.get('Content-Type')?.split(';', 1)[0].trim().toLowerCase()
  return contentType === 'application/json' || contentType?.endsWith('+json') === true
}

function isLoginRedirect(response: Response): boolean {
  if (!response.redirected) {
    return false
  }

  try {
    return new URL(response.url, window.location.origin).pathname === LOGIN_PATH
  } catch {
    return false
  }
}

async function isAuthenticationFailure(response: Response): Promise<boolean> {
  if (response.status === 401 || isLoginRedirect(response)) {
    return true
  }
  if (response.status !== 403 || !isJsonResponse(response)) {
    return false
  }

  try {
    const data: unknown = await response.clone().json()
    return typeof data === 'object' && data !== null && 'detail' in data && data.detail === NOT_AUTHENTICATED_DETAIL
  } catch {
    return false
  }
}

function redirectToLogin(method: ApiRequestMethod, url: string, response: Response): never {
  const next = `${window.location.pathname}${window.location.search}${window.location.hash}`
  window.location.assign(`${LOGIN_PATH}?next=${encodeURIComponent(next)}`)
  throw new Error(`Authentication required for ${method} ${url}; redirecting to login (${responseStatus(response)})`)
}

function isAbortError(error: unknown): boolean {
  return typeof error === 'object' && error !== null && 'name' in error && error.name === 'AbortError'
}

function errorMessage(error: unknown): string {
  return error instanceof Error && error.message ? error.message : String(error)
}

function raiseApiError(error: ApiError): never {
  reportApiError(error)
  throw error
}

function raiseNetworkError(method: ApiRequestMethod, url: string, cause: unknown): never {
  raiseApiError(
    new ApiError(`${method} ${url} failed before receiving a response: ${errorMessage(cause)}`, {
      method,
      url,
      status: null,
      statusText: null,
      body: null,
      cause
    })
  )
}

async function fetchResponse(method: ApiRequestMethod, url: string, init: RequestInit): Promise<Response> {
  try {
    return await fetch(url, init)
  } catch (error) {
    if (isAbortError(error)) {
      throw error
    }
    raiseNetworkError(method, url, error)
  }
}

async function parseResponseBody(response: Response): Promise<unknown> {
  const body = await response.text()
  if (!body) {
    return null
  }
  try {
    return JSON.parse(body) as unknown
  } catch {
    return body
  }
}

async function raiseResponseError(method: ApiRequestMethod, url: string, response: Response, message: string): Promise<never> {
  let body: unknown = null
  try {
    body = await parseResponseBody(response)
  } catch (error) {
    body = `The response body could not be read: ${errorMessage(error)}`
  }
  raiseApiError(
    new ApiError(message, {
      method,
      url,
      status: response.status,
      statusText: response.statusText || null,
      body
    })
  )
}

async function checkResponse(method: ApiRequestMethod, url: string, response: Response): Promise<void> {
  if (await isAuthenticationFailure(response)) {
    redirectToLogin(method, url, response)
  }
  if (!response.ok) {
    await raiseResponseError(method, url, response, `${method} ${url} failed: ${responseStatus(response)}`)
  }
}

async function csrfRequest(method: 'POST' | 'PATCH', url: string, data: object): Promise<Response> {
  const response = await fetchResponse(method, url, {
    method,
    headers: {
      'Content-Type': 'application/json',
      'X-CSRFToken': Cookies.get('csrftoken') || ''
    },
    body: JSON.stringify(data)
  })
  await checkResponse(method, url, response)
  return response
}

function csrfPost(url: string, data: object): Promise<Response> {
  return csrfRequest('POST', url, data)
}

async function fetchAsJson<T = unknown>(url: string, signal?: AbortSignal): Promise<T> {
  const method = 'GET'
  const response = await fetchResponse(method, url, {
    method: 'GET',
    headers: {
      Accept: 'application/json'
    },
    signal
  })

  await checkResponse(method, url, response)
  if (!isJsonResponse(response)) {
    const contentType = response.headers.get('Content-Type') || 'no Content-Type'
    await raiseResponseError(method, url, response, `${method} ${url} failed: expected JSON but received ${contentType} (${responseStatus(response)})`)
  }

  let body: string
  try {
    body = await response.text()
  } catch (error) {
    raiseApiError(
      new ApiError(`${method} ${url} failed while reading JSON (${responseStatus(response)}): ${errorMessage(error)}`, {
        method,
        url,
        status: response.status,
        statusText: response.statusText || null,
        body: null,
        cause: error
      })
    )
  }

  try {
    return JSON.parse(body) as T
  } catch (error) {
    raiseApiError(
      new ApiError(`${method} ${url} returned malformed JSON (${responseStatus(response)}): ${errorMessage(error)}`, {
        method,
        url,
        status: response.status,
        statusText: response.statusText || null,
        body,
        cause: error
      })
    )
  }
}

function csrfPatch(url: string, data: object): Promise<Response> {
  return csrfRequest('PATCH', url, data)
}

function localDatetimeInputValue(date: Date = new Date()): string {
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`
}

function parseLocalDatetimeInput(value: string): Date | null {
  if (!value) return null
  const d = new Date(value)
  return isNaN(d.getTime()) ? null : d
}

function formatDate(s: string): string {
  if (!s) return ''
  const d = new Date(s)
  return isNaN(d.getTime()) ? '' : d.toLocaleDateString()
}

function formatDateTime(s: string): string {
  if (!s) return ''
  const d = new Date(s)
  return isNaN(d.getTime()) ? '' : d.toLocaleString(undefined, { year: 'numeric', month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' })
}

function formatDateRange(start: string | null | undefined, end: string | null | undefined): string {
  return `${formatDate(start ?? '')} - ${formatDate(end ?? '')}`
}

export { ApiError, csrfPost, csrfPatch, fetchAsJson, localDatetimeInputValue, parseLocalDatetimeInput, formatDate, formatDateTime, formatDateRange }
