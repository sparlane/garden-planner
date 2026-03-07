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

export { csrfPost, csrfPatch, fetchAsJson }
