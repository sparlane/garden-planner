import React from 'react'
import PropTypes from 'prop-types'

import 'bootstrap'
import 'bootstrap/dist/css/bootstrap.css'

import { Nav, Navbar } from 'react-bootstrap'

class GPTopBar extends React.Component {
  constructor (props) {
    super(props)

    this.setGardensView = this.setGardensView.bind(this)
    this.setPlantsView = this.setPlantsView.bind(this)
    this.setSeedsView = this.setSeedsView.bind(this)
  }

  setGardensView () {
    this.props.setView('gardens')
  }

  setPlantsView () {
    this.props.setView('plants')
  }

  setSeedsView () {
    this.props.setView('seeds')
  }

  render () {
    return (
      <Navbar expand='lg' bg='secondary' data-bs-theme='dark' collapseOnSelect fixed={top} >
        <Navbar.Toggle aria-controls='responsive-navbar-nav' />
        <Navbar.Collapse id='responsive-navbar-nav'>
          <Nav>
            <Nav.Link href='#gardens' onClick={this.setGardensView}>Gardens</Nav.Link>
            <Nav.Link href='#plants' onClick={this.setPlantsView}>Plants</Nav.Link>
            <Nav.Link href='#seeds' onClick={this.setSeedsView}>Seeds</Nav.Link>
          </Nav>
        </Navbar.Collapse>
      </Navbar>
    )
  }
}
GPTopBar.propTypes = {
  setView: PropTypes.func.isRequired,
}

export { GPTopBar }
