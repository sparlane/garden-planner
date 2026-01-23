import $ from 'jquery'
import Cookies from 'js-cookie'

function csrfPost(url: string, data: object): JQuery.jqXHR {
  return $.ajax({
    url: url,
    method: 'POST',
    data: data,
    beforeSend: function (xhr) {
      xhr.setRequestHeader('X-CSRFToken', Cookies.get('csrftoken'))
    }
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
