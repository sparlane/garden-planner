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

async function csrfRequest(method: 'POST' | 'PATCH' | 'DELETE', url: string, data?: object): Promise<Response> {
  const response = await fetchResponse(method, url, {
    method,
    headers: {
      'Content-Type': 'application/json',
      'X-CSRFToken': Cookies.get('csrftoken') || ''
    },
    body: data === undefined ? undefined : JSON.stringify(data)
  })
  await checkResponse(method, url, response)
  return response
}

function csrfPost(url: string, data: object): Promise<Response> {
  return csrfRequest('POST', url, data)
}

async function csrfPostForm(url: string, data: FormData): Promise<Response> {
  const method = 'POST'
  const response = await fetchResponse(method, url, {
    method,
    headers: {
      'X-CSRFToken': Cookies.get('csrftoken') || ''
    },
    body: data
  })
  await checkResponse(method, url, response)
  return response
}

function csrfDelete(url: string): Promise<Response> {
  return csrfRequest('DELETE', url)
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

// A reservation's remaining hold time, which is what an operator deciding
// about held stock actually reads: "in 3 days" answers "can I sell it this
// week?" where a bare expiry date does not. An open-ended hold has no expiry
// and never lapses, so it gets the fallback rather than an invented deadline.
function formatHoldRemaining(expiresAt: string | null | undefined, fallback = 'No expiry'): string {
  if (!expiresAt) return fallback
  const expiry = new Date(expiresAt)
  if (isNaN(expiry.getTime())) return fallback
  const minutes = Math.round((expiry.getTime() - Date.now()) / 60000)
  const elapsed = minutes <= 0
  const magnitude = Math.abs(minutes)
  const [amount, unit] = magnitude < 60 ? [magnitude, 'minute'] : magnitude < 60 * 24 ? [Math.floor(magnitude / 60), 'hour'] : [Math.floor(magnitude / (60 * 24)), 'day']
  const plural = `${amount} ${unit}${amount === 1 ? '' : 's'}`
  return elapsed ? `lapsed ${plural} ago` : `in ${plural}`
}

// Quantities arrive as zero-padded decimal strings ("24.000000000"). Trim the padding without
// parsing to a number, which would reintroduce float artifacts the decimal column avoids.
function formatQuantity(value: string | number | null | undefined, fallback = ''): string {
  if (value === null || value === undefined || value === '') return fallback
  const s = String(value)
  if (!/^-?\d+(\.\d+)?$/.test(s)) return s
  return s.includes('.') ? s.replace(/\.?0+$/, '') : s
}

// A measured quantity is meaningless without the unit it was measured in, so
// the two are rendered together and never separately.
function formatMeasure(value: string | number | null | undefined, unit: string, fallback = ''): string {
  const quantity = formatQuantity(value)
  return quantity === '' ? fallback : `${quantity} ${unit}`
}

// Money arrives as a fixed four-decimal string ("1.0800") from a DECIMAL(18, 4)
// column, and carries the currency it was recorded in. Unlike a quantity it is
// not trimmed all the way: two decimal places are kept even when they are zero,
// because "1.1" reads as an incomplete price where "1.10" reads as a price. The
// places beyond the second are only shown when they carry a digit, so sub-cent
// unit costs stay exact without padding every ordinary figure. Operates on the
// string throughout — parsing to a number would reintroduce exactly the float
// artifacts the decimal column exists to avoid.
function formatMoney(value: string | number | null | undefined, currencyCode: string, fallback = ''): string {
  if (value === null || value === undefined || value === '') return fallback
  const text = String(value)
  if (!/^-?\d+(\.\d+)?$/.test(text)) return text
  const [whole, fraction = ''] = text.split('.')
  const padded = fraction.padEnd(2, '0')
  const trimmed = padded.length > 2 ? padded.replace(/0+$/, '') : padded
  return `${whole}.${trimmed.padEnd(2, '0')} ${currencyCode}`
}

// Money columns are DECIMAL(18, 4), so a total is summed as scaled integers
// rather than as numbers: 0.1 + 0.2 is not 0.3 in binary floating point, and a
// preview that disagreed with the figure the server then stores would be worse
// than no preview at all. The result is a plain 4-decimal string, ready for
// formatMoney. Anything that is not a decimal string is ignored rather than
// coerced, because a silent zero is the failure this exists to avoid.
function sumMoney(values: Array<string | null | undefined>): string {
  let units = 0n
  for (const value of values) {
    const text = String(value ?? '')
    if (!/^-?\d+(\.\d+)?$/.test(text)) continue
    const negative = text.startsWith('-')
    const [whole, fraction = ''] = (negative ? text.slice(1) : text).split('.')
    const scaled = BigInt(whole + fraction.padEnd(4, '0').slice(0, 4))
    units += negative ? -scaled : scaled
  }
  const negative = units < 0n
  const digits = (negative ? -units : units).toString().padStart(5, '0')
  return `${negative ? '-' : ''}${digits.slice(0, -4)}.${digits.slice(-4)}`
}

// DRF reports a rejected write as {field: [message]}. Forms want one message
// per field, so this flattens it and drops anything that is not shaped that
// way — a network failure has already been published to the global alert.
function errorsByField(error: unknown): Record<string, string> {
  if (!(error instanceof ApiError) || typeof error.body !== 'object' || error.body === null) {
    return {}
  }

  const fields: Record<string, string> = {}
  for (const [field, messages] of Object.entries(error.body as Record<string, unknown>)) {
    if (Array.isArray(messages) && messages.length > 0) {
      fields[field] = String(messages[0])
    } else if (typeof messages === 'string') {
      fields[field] = messages
    }
  }
  return fields
}

export {
  ApiError,
  errorsByField,
  csrfDelete,
  csrfPost,
  csrfPostForm,
  csrfPatch,
  fetchAsJson,
  localDatetimeInputValue,
  parseLocalDatetimeInput,
  formatDate,
  formatDateTime,
  formatDateRange,
  formatHoldRemaining,
  formatQuantity,
  formatMeasure,
  formatMoney,
  sumMoney
}
