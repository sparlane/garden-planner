import Cookies from 'js-cookie'

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
  return fetch(url, {
    method: 'GET',
    headers: {
      Accept: 'application/json'
    },
    signal
  }).then((response) => response.json() as Promise<T>)
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
