import 'bootstrap'
import 'bootstrap/dist/css/bootstrap.css'

import React from 'react'
import { Table } from 'react-bootstrap'
import Select from 'react-select'

import { SeedTrayModel, SeedTray, SeedTrayModelCreate, SeedTrayCreate } from './types/seedtrays'
import { getSeedTrayModels, getSeedTrays, addSeedTrayModel, addSeedTray } from './api/seedtrays'

interface SeedTrayModelNewProps {
  done: () => void
}

class SeedTrayModelNew extends React.Component<SeedTrayModelNewProps, SeedTrayModelCreate> {
  constructor(props: SeedTrayModelNewProps) {
    super(props)

    this.state = {
      identifier: '',
      description: '',
      height: 0,
      x_size: 0,
      y_size: 0,
      x_cells: 0,
      y_cells: 0,
      cell_size_ml: 0
    }
  }

  private updateText = (field: keyof Pick<SeedTrayModelCreate, 'identifier' | 'description'>) => (event: React.ChangeEvent<HTMLInputElement>) => {
    this.setState({ [field]: event.target.value } as Pick<SeedTrayModelCreate, typeof field>)
  }

  private updateNumber = (field: keyof Omit<SeedTrayModelCreate, 'identifier' | 'description'>) => (event: React.ChangeEvent<HTMLInputElement>) => {
    this.setState({ [field]: parseInt(event.target.value, 10) || 0 } as Pick<SeedTrayModelCreate, typeof field>)
  }

  private createSeedTrayModel = () => {
    addSeedTrayModel(this.state).then((response) => response.ok && this.props.done())
  }

  render() {
    return (
      <tr>
        <td></td>
        <td>
          <input type="text" name="identifier" onChange={this.updateText('identifier')} />
        </td>
        <td>
          <input type="text" name="description" onChange={this.updateText('description')} />
        </td>
        <td>
          <input type="number" name="x_size" onChange={this.updateNumber('x_size')} /> x
          <input type="number" name="y_size" onChange={this.updateNumber('y_size')} /> x
          <input type="number" name="height" onChange={this.updateNumber('height')} />
          (<input type="number" name="x_cells" onChange={this.updateNumber('x_cells')} /> x
          <input type="number" name="y_cells" onChange={this.updateNumber('y_cells')} />)
        </td>
        <td>
          <input type="number" name="cell_size_ml" onChange={this.updateNumber('cell_size_ml')} />
        </td>
        <td>
          <button type="button" className="btn btn-primary" onClick={this.createSeedTrayModel}>
            Create
          </button>
        </td>
      </tr>
    )
  }
}

interface SeedTrayModelsTableState {
  showAddRow: boolean
  seedTrayModels: Array<SeedTrayModel>
}

class SeedTrayModelsTable extends React.Component<undefined, SeedTrayModelsTableState> {
  constructor(props: undefined) {
    super(props)

    this.state = {
      showAddRow: false,
      seedTrayModels: []
    }
  }

  componentDidMount() {
    this.fetchSeedTrayModels()
  }

  private hideAddRow = () => {
    this.setState({ showAddRow: false })
    this.fetchSeedTrayModels()
  }

  private showAddRow = () => this.setState({ showAddRow: true })

  private fetchSeedTrayModels = async () => {
    this.setState({ seedTrayModels: await getSeedTrayModels() })
  }

  render() {
    return (
      <Table>
        <thead>
          <tr>
            <th>
              ID<button onClick={this.showAddRow}>+</button>
            </th>
            <th>Name</th>
            <th>Description</th>
            <th>Size mm (cells)</th>
            <th>Cell Size (ml)</th>
          </tr>
        </thead>
        <tbody>
          {this.state.showAddRow && <SeedTrayModelNew key="add" done={this.hideAddRow} />}
          {this.state.seedTrayModels.map((model) => (
            <tr key={model.pk}>
              <td>{model.pk}</td>
              <td>{model.identifier}</td>
              <td>{model.description}</td>
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

interface SeedTrayAddProps {
  done: () => void
  models: Array<SeedTrayModel>
}

class SeedTrayAdd extends React.Component<SeedTrayAddProps, SeedTrayCreate> {
  constructor(props: SeedTrayAddProps) {
    super(props)

    this.state = {
      model: undefined,
      notes: ''
    }
  }

  private updateModel = (newValue: { value: number; label: string } | null) => {
    this.setState({ model: newValue?.value || undefined })
  }

  private updateNotes = (event: React.ChangeEvent<HTMLInputElement>) => {
    this.setState({ notes: event.target.value })
  }

  private createSeedTray = () => {
    if (!this.state.model) {
      return
    }
    addSeedTray(this.state).then((response) => response.ok && this.props.done())
  }

  render() {
    return (
      <tr>
        <td></td>
        <td>
          <Select name="model" onChange={this.updateModel} options={this.props.models.map((model) => ({ value: model.pk, label: model.identifier }))} />
        </td>
        <td></td>
        <td>
          <input type="text" name="notes" onChange={this.updateNotes} />
        </td>
        <td>
          <button type="button" className="btn btn-primary" onClick={this.createSeedTray}>
            Create
          </button>
        </td>
      </tr>
    )
  }
}

interface SeedTraysTableState {
  showAddRow: boolean
  seedTrays: Array<SeedTray>
  seedTrayModelList: Array<SeedTrayModel>
  seedTrayModels: { [key: number]: SeedTrayModel }
}

class SeedTraysTable extends React.Component<undefined, SeedTraysTableState> {
  constructor(props: undefined) {
    super(props)

    this.state = {
      showAddRow: false,
      seedTrays: [],
      seedTrayModelList: [],
      seedTrayModels: {}
    }
  }

  componentDidMount() {
    this.fetchSeedTrays()
  }

  private showAddRow = () => this.setState({ showAddRow: true })

  private hideAddRow = () => {
    this.setState({ showAddRow: false })
    this.fetchSeedTrays()
  }

  private fetchSeedTrays = async () => {
    const [seedTraysData, seedTrayModelsData] = (await Promise.all([getSeedTrays(), getSeedTrayModels()])) as [Array<SeedTray>, Array<SeedTrayModel>]

    const seedTrayModelsMap: { [key: number]: SeedTrayModel } = seedTrayModelsData.reduce((acc: { [key: number]: SeedTrayModel }, model: SeedTrayModel) => {
      acc[model.pk] = model
      return acc
    }, {})

    this.setState({ seedTrays: seedTraysData, seedTrayModels: seedTrayModelsMap, seedTrayModelList: seedTrayModelsData })
  }

  render() {
    return (
      <Table>
        <thead>
          <tr>
            <th>
              ID<button onClick={this.showAddRow}>+</button>
            </th>
            <th>Model</th>
            <th>Created</th>
            <th>Notes</th>
          </tr>
        </thead>
        <tbody>
          {this.state.showAddRow && <SeedTrayAdd key="add" done={this.hideAddRow} models={this.state.seedTrayModelList} />}
          {this.state.seedTrays.map((tray) => (
            <tr key={tray.pk}>
              <td>
                <a href={`/seedtrays/seedtray/${tray.pk}/`}>{tray.pk}</a>
              </td>
              <td>{tray.model && this.state.seedTrayModels[tray.model]?.identifier}</td>
              <td>{tray.created}</td>
              <td>{tray.notes}</td>
            </tr>
          ))}
        </tbody>
      </Table>
    )
  }
}

export { SeedTrayModelsTable, SeedTraysTable }
