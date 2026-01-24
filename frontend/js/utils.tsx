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

async function fetchAsJson<T = unknown>(url: string): Promise<T> {
  return fetch(url, {
    method: 'GET',
    headers: {
      Accept: 'application/json'
    }
  }).then((response) => response.json() as Promise<T>)
}

export { csrfPost, fetchAsJson }
