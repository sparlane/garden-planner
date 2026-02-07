import React from 'react'
import * as ReactDOM from 'react-dom/client'

import { SeedTray, SeedTrayCell, SeedTrayModel } from '../types/seedtrays'
import { getSeedTrayModels, getSeedTrays, getSeedTrayCells } from '../api/seedtrays'
import { Table } from 'react-bootstrap'
import { SeedTrayPlanting } from '../types/plantings'
import { getPlantingSeedTray } from '../api/plantings'
import { SeedPacketDetails } from '../types/seeds'
import { getSeedPacketsCurrent } from '../api/seeds'

interface SeedTrayDetailsProps {
  seedTrayPk: number
}

interface SeedTrayDetailsState {
  seedTray?: SeedTray
  seedTrayModel?: SeedTrayModel
  seedTrayCells?: (SeedTrayCell | undefined)[][]
  plantings?: Array<SeedTrayPlanting>
  seeds?: { [key: number]: SeedPacketDetails }
}

function buildCellGrid(model: SeedTrayModel | undefined, cells: Array<SeedTrayCell>): (SeedTrayCell | undefined)[][] {
  if (!model) return []
  const grid: (SeedTrayCell | undefined)[][] = Array.from({ length: model.x_cells }, () => Array.from({ length: model.y_cells }, () => undefined))
  cells.forEach((cell) => {
    if (cell.x_position < 0 || cell.x_position >= model.x_cells) return
    if (cell.y_position < 0 || cell.y_position >= model.y_cells) return

    if (grid[cell.x_position][cell.y_position] === undefined) {
      grid[cell.x_position][cell.y_position] = cell
    }
  })
  return grid
}

class SeedTrayDetails extends React.Component<SeedTrayDetailsProps, SeedTrayDetailsState> {
  constructor(props: SeedTrayDetailsProps) {
    super(props)

    this.state = {}
  }

  async componentDidMount() {
    const [seedTrayModels, seedTrays, seedTrayCells, seedTrayPlantings, seedPacketDetails] = await Promise.all([
      getSeedTrayModels(),
      getSeedTrays(),
      getSeedTrayCells(this.props.seedTrayPk),
      getPlantingSeedTray(this.props.seedTrayPk),
      getSeedPacketsCurrent()
    ])
    const seedTray = seedTrays.find((t) => t.pk === this.props.seedTrayPk)
    const seedTrayModel = seedTrayModels.find((m) => m.pk === seedTray?.model)
    this.setState({
      seedTray: seedTray,
      seedTrayModel: seedTrayModel,
      plantings: seedTrayPlantings,
      seedTrayCells: buildCellGrid(seedTrayModel, seedTrayCells),
      seeds: seedPacketDetails.reduce((acc: { [key: number]: SeedPacketDetails }, seed) => {
        acc[seed.pk] = seed
        return acc
      }, {})
    })
  }

  render() {
    const { seedTray, seedTrayModel, seedTrayCells } = this.state
    if (!seedTray) {
      return <div>Loading...</div>
    }

    const cellTotals: { [key: number]: number } = {}
    this.state.plantings?.forEach((p) => {
      if (p.removed) return
      p.cell_plantings?.forEach((cp) => {
        cellTotals[cp.cell] = (cellTotals[cp.cell] || 0) + cp.quantity
      })
    })

    return (
      <div>
        <h1>Seed Tray Details (ID: {seedTray.pk})</h1>
        <p>
          Model: {seedTrayModel?.identifier} ({seedTrayModel?.description})
        </p>
        <p>Created: {seedTray.created}</p>
        <p>Notes: {seedTray.notes}</p>
        Plantings:
        <Table border={1} cellPadding={5} cellSpacing={0}>
          <thead>
            <tr>
              <th>Planting ID</th>
              <th>Planted On</th>
              <th>Quantity</th>
              <th>Seeds Used</th>
              <th>Notes</th>
              <th>Removed</th>
            </tr>
          </thead>
          <tbody>
            {this.state.plantings?.map((planting) => {
              const seeds = this.state.seeds?.[planting.seeds_used]
              return (
                <tr key={planting.pk}>
                  <td>{planting.pk}</td>
                  <td>{planting.planted}</td>
                  <td>{planting.quantity}</td>
                  <td>
                    {seeds?.plant} - {seeds?.variety}
                  </td>
                  <td>{planting.notes}</td>
                  <td>{planting.removed ? 'Yes' : ''}</td>
                </tr>
              )
            })}
          </tbody>
        </Table>
        <Table border={2} cellPadding={5} cellSpacing={0}>
          <tbody>
            {seedTrayModel &&
              seedTrayCells?.map((row, r) => (
                <tr key={r}>
                  {row.map((cell, c) => (
                    <td key={c} style={{ textAlign: 'center' }}>
                      <div>{cell?.pk ?? ''}</div>
                      <div style={{ fontWeight: 'bold' }}>{cell ? cellTotals[cell.pk] || 0 : ''}</div>
                    </td>
                  ))}
                </tr>
              ))}
          </tbody>
        </Table>
      </div>
    )
  }
}

function showSeedTrayDetails(elem: string, seedTrayPk: number) {
  const root = ReactDOM.createRoot(document.getElementById(elem) as HTMLElement)
  root.render(<SeedTrayDetails seedTrayPk={seedTrayPk} />)
}

globalThis.showSeedTrayDetails = showSeedTrayDetails
