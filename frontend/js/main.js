import 'bootstrap'
import 'bootstrap/dist/css/bootstrap.css'

import React from 'react'
import * as ReactDOM from 'react-dom/client'

class FrontEndPage extends React.Component {
    render() {
        return (
            <b>Hello</b>
        )
    }
}

function createFrontEnd (elementId) {
  const div = ReactDOM.createRoot(document.getElementById(elementId))
  div.render(<><FrontEndPage /></>)
}

globalThis.createFrontEnd = createFrontEnd
