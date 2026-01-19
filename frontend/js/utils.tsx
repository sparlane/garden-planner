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

export { csrfPost }
