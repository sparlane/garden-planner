import 'bootstrap'
import 'bootstrap/dist/css/bootstrap.css'

import React from 'react'
import { Table } from 'react-bootstrap'

import { SeedTrayModel, SeedTray } from './types/seedtrays'
import { getSeedTrayModels, getSeedTrays } from './api/seedtrays'

interface SeedTrayModelsTableState {
  seedTrayModels: Array<SeedTrayModel>
}

class SeedTrayModelsTable extends React.Component<undefined, SeedTrayModelsTableState> {
  constructor(props: undefined) {
    super(props)

    this.state = {
      seedTrayModels: []
    }

    this.fetchSeedTrayModels = this.fetchSeedTrayModels.bind(this)
  }

  componentDidMount() {
    this.fetchSeedTrayModels()
  }

  async fetchSeedTrayModels() {
    this.setState({ seedTrayModels: await getSeedTrayModels() })
  }

  render() {
    return (
      <Table>
        <thead>
          <tr>
            <th>ID</th>
            <th>Name</th>
            <th>Size mm (cells)</th>
            <th>Cell Size (ml)</th>
          </tr>
        </thead>
        <tbody>
          {this.state.seedTrayModels.map((model) => (
            <tr key={model.pk}>
              <td>{model.pk}</td>
              <td>{model.identifier}</td>
              <td>
                {model.x_size}x{model.y_size}x{model.height} ({model.x_cells}x{model.y_cells})
              </td>
              <td>{model.cell_size_ml}</td>
            </tr>
          ))}
        </tbody>
      </Table>
    )
  }
}

interface SeedTraysTableState {
  seedTrays: Array<SeedTray>
  seedTrayModels: { [key: number]: SeedTrayModel }
}

class SeedTraysTable extends React.Component<undefined, SeedTraysTableState> {
  constructor(props: undefined) {
    super(props)

    this.state = {
      seedTrays: [],
      seedTrayModels: {}
    }

    this.fetchSeedTrays = this.fetchSeedTrays.bind(this)
  }

  componentDidMount() {
    this.fetchSeedTrays()
  }

  async fetchSeedTrays() {
    const [seedTraysData, seedTrayModelsData] = (await Promise.all([getSeedTrays(), getSeedTrayModels()])) as [Array<SeedTray>, Array<SeedTrayModel>]

    const seedTrayModelsMap: { [key: number]: SeedTrayModel } = seedTrayModelsData.reduce((acc: { [key: number]: SeedTrayModel }, model: SeedTrayModel) => {
      acc[model.pk] = model
      return acc
    }, {})

    this.setState({ seedTrays: seedTraysData, seedTrayModels: seedTrayModelsMap })
  }

  render() {
    return (
      <Table>
        <thead>
          <tr>
            <th>ID</th>
            <th>Model</th>
            <th>Notes</th>
          </tr>
        </thead>
        <tbody>
          {this.state.seedTrays.map((tray) => (
            <tr key={tray.pk}>
              <td>{tray.pk}</td>
              <td>{this.state.seedTrayModels[tray.model]?.identifier}</td>
              <td>{tray.notes}</td>
            </tr>
          ))}
        </tbody>
      </Table>
    )
  }
}

export { SeedTrayModelsTable, SeedTraysTable }
