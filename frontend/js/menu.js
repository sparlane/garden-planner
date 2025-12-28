import React from 'react'
import PropTypes from 'prop-types'

import 'bootstrap'
import 'bootstrap/dist/css/bootstrap.css'

import { Nav, Navbar, NavDropdown } from 'react-bootstrap'

class GPTopBar extends React.Component {
  constructor(props) {
    super(props)

    this.setGardensView = this.setGardensView.bind(this)
    this.setPlantsView = this.setPlantsView.bind(this)
    this.setSeedsSupplierView = this.setSeedsSupplierView.bind(this)
    this.setSeedsView = this.setSeedsView.bind(this)
    this.setSeedsStockView = this.setSeedsStockView.bind(this)
    this.setSeedTrayModelView = this.setSeedTrayModelView.bind(this)
    this.setSeedTrayView = this.setSeedTrayView.bind(this)
    this.setPlantingSeedTrayView = this.setPlantingSeedTrayView.bind(this)
    this.setPlantingGardenSquareView = this.setPlantingGardenSquareView.bind(this)
  }

  setGardensView() {
    this.props.setView('gardens')
  }

  setPlantsView() {
    this.props.setView('plants')
  }

  setSeedsSupplierView() {
    this.props.setView('seeds-supplier')
  }

  setSeedsView() {
    this.props.setView('seeds-seed')
  }

  setSeedsStockView() {
    this.props.setView('seeds-stock')
  }

  setPlantingSeedTrayView() {
    this.props.setView('planting-seedtrays')
  }

  setPlantingGardenSquareView() {
    this.props.setView('planting-gardensquare')
  }

  setSeedTrayModelView() {
    this.props.setView('seedtrays-models')
  }

  setSeedTrayView() {
    this.props.setView('seedtrays')
  }

  render() {
    return (
      <Navbar expand="lg" bg="secondary" data-bs-theme="dark" collapseOnSelect fixed={top}>
        <Navbar.Toggle aria-controls="responsive-navbar-nav" />
        <Navbar.Collapse id="responsive-navbar-nav">
          <Nav>
            <Nav.Link href="#gardens" onClick={this.setGardensView}>
              Gardens
            </Nav.Link>
            <Nav.Link href="#plants" onClick={this.setPlantsView}>
              Plants
            </Nav.Link>
            <NavDropdown title="Seeds">
              <Nav.Link href="#seeds-supplier" onClick={this.setSeedsSupplierView}>
                Suppliers
              </Nav.Link>
              <Nav.Link href="#seeds" onClick={this.setSeedsView}>
                Seeds
              </Nav.Link>
              <Nav.Link href="#seeds-stock" onClick={this.setSeedsStockView}>
                Stock
              </Nav.Link>
            </NavDropdown>
            <NavDropdown title="Seed Trays">
              <Nav.Link href="#seedtrays-models" onClick={this.setSeedTrayModelView}>
                Seed Tray Models
              </Nav.Link>
              <Nav.Link href="#seedtrays" onClick={this.setSeedTrayView}>
                Seed Trays
              </Nav.Link>
            </NavDropdown>
            <NavDropdown title="Planting">
              <Nav.Link href="#planting-seedtray" onClick={this.setPlantingSeedTrayView}>
                Seed Trays
              </Nav.Link>
              <Nav.Link href="#planting-gardensquare" onClick={this.setPlantingGardenSquareView}>
                Garden Squares
              </Nav.Link>
            </NavDropdown>
          </Nav>
        </Navbar.Collapse>
      </Navbar>
    )
  }
}
GPTopBar.propTypes = {
  setView: PropTypes.func.isRequired
}

export { GPTopBar }
