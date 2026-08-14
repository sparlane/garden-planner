import React from 'react'

import 'bootstrap'
import 'bootstrap/dist/css/bootstrap.css'

import { Nav, Navbar, NavDropdown } from 'react-bootstrap'
import { NavLink, useLocation } from 'react-router'
import { Workspace } from './types/workspace'

interface GPTopBarProps {
  workspace: Workspace
}

function GPTopBar({ workspace }: GPTopBarProps) {
  const { pathname } = useLocation()
  const seedsActive = pathname === '/seeds' || pathname.startsWith('/seeds/')
  const seedTraysActive = pathname === '/seedtrays' || pathname.startsWith('/seedtrays/')
  const plantingActive = pathname === '/plantings' || pathname.startsWith('/plantings/') || pathname === '/health'
  const inventoryActive = pathname === '/inventory' || pathname.startsWith('/inventory/') || pathname.startsWith('/applications') || pathname.startsWith('/locations')
  const salesActive = pathname === '/sales' || pathname.startsWith('/sales/')

  return (
    <Navbar expand="lg" bg="secondary" data-bs-theme="dark" collapseOnSelect>
      <Navbar.Brand as={NavLink} to="/gardens">
        {workspace.name} · {workspace.mode === 'garden' ? 'Garden' : 'Nursery'}
      </Navbar.Brand>
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
          {workspace.mode === 'nursery' && (
            <NavDropdown title="Sales" active={salesActive}>
              <NavDropdown.Item as={NavLink} to="/sales/orders">
                Orders
              </NavDropdown.Item>
              <NavDropdown.Item as={NavLink} to="/sales/customers">
                Customers
              </NavDropdown.Item>
            </NavDropdown>
          )}
          <NavDropdown title="Seed Trays" active={seedTraysActive}>
            <NavDropdown.Item as={NavLink} to="/seedtrays/models">
              Seed Tray Models
            </NavDropdown.Item>
            <NavDropdown.Item as={NavLink} to="/seedtrays" end>
              Seed Trays
            </NavDropdown.Item>
          </NavDropdown>
          <NavDropdown title="Planting" active={plantingActive}>
            {workspace.mode === 'nursery' && (
              <>
                <NavDropdown.Item as={NavLink} to="/plantings/register">
                  Plant register
                </NavDropdown.Item>
                <NavDropdown.Item as={NavLink} to="/plantings/cohorts">
                  Cohort inventory
                </NavDropdown.Item>
                <NavDropdown.Item as={NavLink} to="/plantings/growth-setup">
                  Growth stages and grades
                </NavDropdown.Item>
                <NavDropdown.Item as={NavLink} to="/plantings/production-planning">
                  Production planning
                </NavDropdown.Item>
                <NavDropdown.Item as={NavLink} to="/health">
                  Plant health
                </NavDropdown.Item>
              </>
            )}
            <NavDropdown.Item as={NavLink} to="/plantings/batches">
              Batches
            </NavDropdown.Item>
            <NavDropdown.Item as={NavLink} to="/plantings/seedtrays">
              Seed Trays
            </NavDropdown.Item>
            <NavDropdown.Item as={NavLink} to="/plantings/garden-squares">
              Garden Squares
            </NavDropdown.Item>
            <NavDropdown.Item as={NavLink} to="/plantings/harvests">
              Harvests
            </NavDropdown.Item>
            <NavDropdown.Item as={NavLink} to="/plantings/yield">
              Yield
            </NavDropdown.Item>
          </NavDropdown>
          <NavDropdown title="Inventory" active={inventoryActive}>
            <NavDropdown.Item as={NavLink} to="/inventory" end>
              Catalog
            </NavDropdown.Item>
            <NavDropdown.Item as={NavLink} to="/locations">
              Locations
            </NavDropdown.Item>
            <NavDropdown.Item as={NavLink} to="/inventory/receipts">
              Receiving
            </NavDropdown.Item>
            {workspace.mode === 'nursery' && (
              <NavDropdown.Item as={NavLink} to="/inventory/stocktakes">
                Stocktakes
              </NavDropdown.Item>
            )}
            <NavDropdown.Item as={NavLink} to="/applications">
              Input applications
            </NavDropdown.Item>
          </NavDropdown>
          <Nav.Link as={NavLink} to="/settings">
            Settings
          </Nav.Link>
          {workspace.mode === 'nursery' && (
            <Nav.Link as={NavLink} to="/work">
              Work
            </Nav.Link>
          )}
          <Nav.Link as={NavLink} to="/labels">
            Labels
          </Nav.Link>
          <Nav.Link as={NavLink} to="/scan">
            Scan
          </Nav.Link>
        </Nav>
      </Navbar.Collapse>
    </Navbar>
  )
}

export { GPTopBar }
