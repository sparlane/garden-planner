import Cookies from 'js-cookie'

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

function redirectToLogin(url: string, response: Response): never {
  const next = `${window.location.pathname}${window.location.search}${window.location.hash}`
  window.location.assign(`${LOGIN_PATH}?next=${encodeURIComponent(next)}`)
  throw new Error(`Authentication required while fetching ${url}; redirecting to login (${responseStatus(response)})`)
}

function csrfPost(url: string, data: object): Promise<Response> {
  return fetch(url, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'X-CSRFToken': Cookies.get('csrftoken') || ''
    },
    body: JSON.stringify(data)
  }).then((response) => {
    if (!response.ok) {
      throw new Error(`Failed to post data to ${url} ${response.status}: ${response.statusText}`)
    }
    return response
  })
}

async function fetchAsJson<T = unknown>(url: string, signal?: AbortSignal): Promise<T> {
  const response = await fetch(url, {
    method: 'GET',
    headers: {
      Accept: 'application/json'
    },
    signal
  })

  if (await isAuthenticationFailure(response)) {
    redirectToLogin(url, response)
  }
  if (!response.ok) {
    throw new Error(`Failed to fetch JSON from ${url}: ${responseStatus(response)}`)
  }
  if (!isJsonResponse(response)) {
    const contentType = response.headers.get('Content-Type') || 'no Content-Type'
    throw new Error(`Failed to fetch JSON from ${url}: expected JSON but received ${contentType} (${responseStatus(response)})`)
  }

  try {
    return (await response.json()) as T
  } catch (error) {
    const detail = error instanceof Error ? `: ${error.message}` : ''
    throw new Error(`Failed to parse JSON from ${url} (${responseStatus(response)})${detail}`)
  }
}

function csrfPatch(url: string, data: object): Promise<Response> {
  return fetch(url, {
    method: 'PATCH',
    headers: {
      'Content-Type': 'application/json',
      'X-CSRFToken': Cookies.get('csrftoken') || ''
    },
    body: JSON.stringify(data)
  }).then((response) => {
    if (!response.ok) {
      throw new Error(`Failed to patch data to ${url} ${response.status}: ${response.statusText}`)
    }
    return response
  })
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

export { csrfPost, csrfPatch, fetchAsJson, localDatetimeInputValue, parseLocalDatetimeInput, formatDate, formatDateTime, formatDateRange }
