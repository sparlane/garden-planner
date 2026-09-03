import 'bootstrap'
import 'bootstrap/dist/css/bootstrap.css'

import React from 'react'
import { Form, Table } from 'react-bootstrap'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link } from 'react-router'

import { SerializedPhysicalState } from './types/inventory'
import { Location } from './types/locations'
import { SeedTrayModel, SeedTrayModelCreate } from './types/seedtrays'
import { getSeedTrayModels, getSeedTrays, addSeedTrayModel } from './api/seedtrays'
import { getLocations } from './api/locations'
import { formatDate } from './utils'
import { queryKeys } from './query'

interface SeedTrayModelNewProps {
  done: () => void
  createModel: (data: SeedTrayModelCreate) => Promise<void>
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

  private createSeedTrayModel = async () => {
    await this.props.createModel(this.state)
    this.props.done()
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

function SeedTrayModelsTable() {
  const queryClient = useQueryClient()
  const [showAddRow, setShowAddRow] = React.useState(false)
  const { data: seedTrayModels = [] } = useQuery({
    queryKey: queryKeys.seedTrays.models,
    queryFn: ({ signal }) => getSeedTrayModels(signal)
  })
  const modelMutation = useMutation({
    mutationFn: addSeedTrayModel,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: queryKeys.seedTrays.models })
  })

  async function createModel(data: SeedTrayModelCreate) {
    await modelMutation.mutateAsync(data)
  }

  return (
    <Table>
      <thead>
        <tr>
          <th>
            ID<button onClick={() => setShowAddRow(true)}>+</button>
          </th>
          <th>Name</th>
          <th>Description</th>
          <th>Size mm (cells)</th>
          <th>Cell Size (ml)</th>
        </tr>
      </thead>
      <tbody>
        {showAddRow && <SeedTrayModelNew key="add" createModel={createModel} done={() => setShowAddRow(false)} />}
        {seedTrayModels.map((model) => (
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

function SeedTraysTable() {
  const [physicalState, setPhysicalState] = React.useState<SerializedPhysicalState | ''>('')
  const [location, setLocation] = React.useState<number>()
  const [inUse, setInUse] = React.useState<'' | 'true' | 'false'>('')
  const { data: seedTrays = [] } = useQuery({
    queryKey: [...queryKeys.seedTrays.trays, physicalState, location, inUse],
    queryFn: ({ signal }) =>
      getSeedTrays(signal, {
        physical_state: physicalState || undefined,
        location,
        in_use: inUse ? inUse === 'true' : undefined
      })
  })
  const { data: seedTrayModels = [] } = useQuery({
    queryKey: queryKeys.seedTrays.models,
    queryFn: ({ signal }) => getSeedTrayModels(signal)
  })
  const { data: locations = [] } = useQuery({
    queryKey: queryKeys.locations.list('active'),
    queryFn: ({ signal }) => getLocations(signal, true)
  })
  const seedTrayModelsMap = seedTrayModels.reduce<Record<number, SeedTrayModel>>((models, model) => {
    models[model.pk] = model
    return models
  }, {})

  const locationsMap = locations.reduce<Record<number, Location>>((result, entry) => {
    result[entry.pk] = entry
    return result
  }, {})

  return (
    <>
      <div className="d-flex gap-2 mb-3">
        <Form.Select aria-label="Filter tray state" value={physicalState} onChange={(event) => setPhysicalState(event.target.value as SerializedPhysicalState | '')}>
          <option value="">All physical states</option>
          {['available', 'quarantined', 'lost', 'retired', 'dispatched', 'returned'].map((state) => (
            <option key={state} value={state}>
              {state}
            </option>
          ))}
        </Form.Select>
        <Form.Select aria-label="Filter tray location" value={location ?? ''} onChange={(event) => setLocation(event.target.value ? Number(event.target.value) : undefined)}>
          <option value="">All locations</option>
          {locations.map((entry) => (
            <option key={entry.pk} value={entry.pk}>
              {entry.name}
            </option>
          ))}
        </Form.Select>
        <Form.Select aria-label="Filter trays in use" value={inUse} onChange={(event) => setInUse(event.target.value as '' | 'true' | 'false')}>
          <option value="">Any cultivation use</option>
          <option value="true">In use</option>
          <option value="false">Not in use</option>
        </Form.Select>
      </div>
      <Table>
        <thead>
          <tr>
            <th>
              ID{' '}
              <Link className="btn btn-primary btn-sm" to="/inventory/receipts?new=1">
                Receive
              </Link>
            </th>
            <th>Model</th>
            <th>Asset</th>
            <th>State</th>
            <th>Location</th>
            <th>In use</th>
            <th>Acquisition cost</th>
            <th>Created</th>
            <th>Notes</th>
          </tr>
        </thead>
        <tbody>
          {seedTrays.map((tray) => (
            <tr key={tray.pk}>
              <td>
                <Link to={`/seedtrays/${tray.pk}`}>{tray.pk}</Link>
              </td>
              <td>{tray.model && seedTrayModelsMap[tray.model]?.identifier}</td>
              <td>{tray.inventory.asset_code}</td>
              <td>{tray.inventory.physical_state}</td>
              <td>{tray.inventory.current_location ? locationsMap[tray.inventory.current_location]?.name : 'Off hand'}</td>
              <td>{tray.inventory.in_use ? 'Yes' : 'No'}</td>
              <td>{tray.inventory.acquisition_cost === null ? 'Unknown' : `${tray.inventory.acquisition_cost} ${tray.inventory.currency_code}`}</td>
              <td>{formatDate(tray.created)}</td>
              <td>{tray.notes}</td>
            </tr>
          ))}
        </tbody>
      </Table>
    </>
  )
}

export { SeedTrayModelsTable, SeedTraysTable }
