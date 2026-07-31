import React from 'react'

import 'bootstrap'
import 'bootstrap/dist/css/bootstrap.css'

import { Nav, Navbar, NavDropdown } from 'react-bootstrap'
import { NavLink, useLocation } from 'react-router-dom'

function GPTopBar() {
  const { pathname } = useLocation()
  const seedsActive = pathname === '/seeds' || pathname.startsWith('/seeds/')
  const seedTraysActive = pathname === '/seedtrays' || pathname.startsWith('/seedtrays/')
  const plantingActive = pathname === '/plantings' || pathname.startsWith('/plantings/')

  return (
    <Navbar expand="lg" bg="secondary" data-bs-theme="dark" collapseOnSelect>
      <Navbar.Toggle aria-controls="responsive-navbar-nav" />
      <Navbar.Collapse id="responsive-navbar-nav">
        <Nav>
          <Nav.Link as={NavLink} to="/gardens">
            Gardens
          </Nav.Link>
          <Nav.Link as={NavLink} to="/plants">
            Plants
          </Nav.Link>
          <NavDropdown title="Seeds" active={seedsActive}>
            <NavDropdown.Item as={NavLink} to="/seeds/suppliers">
              Suppliers
            </NavDropdown.Item>
            <NavDropdown.Item as={NavLink} to="/seeds" end>
              Seeds
            </NavDropdown.Item>
            <NavDropdown.Item as={NavLink} to="/seeds/stock">
              Stock
            </NavDropdown.Item>
          </NavDropdown>
          <NavDropdown title="Seed Trays" active={seedTraysActive}>
            <NavDropdown.Item as={NavLink} to="/seedtrays/models">
              Seed Tray Models
            </NavDropdown.Item>
            <NavDropdown.Item as={NavLink} to="/seedtrays" end>
              Seed Trays
            </NavDropdown.Item>
          </NavDropdown>
          <NavDropdown title="Planting" active={plantingActive}>
            <NavDropdown.Item as={NavLink} to="/plantings/seedtrays">
              Seed Trays
            </NavDropdown.Item>
            <NavDropdown.Item as={NavLink} to="/plantings/garden-squares">
              Garden Squares
            </NavDropdown.Item>
          </NavDropdown>
        </Nav>
      </Navbar.Collapse>
    </Navbar>
  )
}

export { GPTopBar }
