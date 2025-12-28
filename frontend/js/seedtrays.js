import 'bootstrap'
import 'bootstrap/dist/css/bootstrap.css'

import React from 'react'
import { Table } from 'react-bootstrap'

class SeedTrayModelsTable extends React.Component {
  constructor(props) {
    super(props)

    this.state = {
      seedTrayModels: []
    }

    this.fetchSeedTrayModels = this.fetchSeedTrayModels.bind(this)
  }

  componentDidMount() {
    this.fetchSeedTrayModels()
  }

  fetchSeedTrayModels() {
    fetch('/seedtrays/seedtraymodels/')
      .then((response) => response.json())
      .then((data) => {
        this.setState({ seedTrayModels: data })
      })
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

class SeedTraysTable extends React.Component {
  constructor(props) {
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

  fetchSeedTrays() {
    Promise.all([fetch('/seedtrays/seedtrays/').then((response) => response.json()), fetch('/seedtrays/seedtraymodels/').then((response) => response.json())])
      .then(([seedTraysData, seedTrayModelsData]) => [
        seedTraysData,
        seedTrayModelsData.reduce((acc, model) => {
          acc[model.pk] = model
          return acc
        }, {})
      ])
      .then(([seedTraysData, seedTrayModelsData]) => {
        this.setState({ seedTrays: seedTraysData, seedTrayModels: seedTrayModelsData })
      })
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
