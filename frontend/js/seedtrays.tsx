import 'bootstrap'
import 'bootstrap/dist/css/bootstrap.css'

import React from 'react'
import { Alert, Button, Form, Table } from 'react-bootstrap'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link } from 'react-router'

import { SerializedPhysicalState } from './types/inventory'
import { Location } from './types/locations'
import { SeedTrayModel, SeedTrayModelCreate } from './types/seedtrays'
import { getSeedTrayModels, getSeedTrays, addSeedTrayModel, updateSeedTrayModel } from './api/seedtrays'
import { getLocations } from './api/locations'
import { CatalogSearch, CatalogValues, CorrectionDialog, DuplicateWarning, ReplacedByNote, RetireButton, RetiredBadge, retiredRowClass } from './catalog'
import { errorsByField, formatDate } from './utils'
import { queryKeys, searchedKey } from './query'

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
          <DuplicateWarning collection="/seedtrays/seedtraymodels/" field="identifier" name={this.state.identifier} />
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

// The dimensions a tray model records are not all the same kind of fact. The
// cell grid is built into every tray received against the model — its cells sit
// at those coordinates and sowings name them — so the server freezes it once a
// tray exists and a change supersedes the model instead. The outer measurements
// and the cell volume describe those same trays, so they stay editable.
const TRAY_MODEL_MEASUREMENTS: Array<{ name: keyof SeedTrayModelCreate; label: string }> = [
  { name: 'x_size', label: 'Width (mm)' },
  { name: 'y_size', label: 'Depth (mm)' },
  { name: 'height', label: 'Height (mm)' },
  { name: 'cell_size_ml', label: 'Cell volume (ml)' }
]

function trayModelValues(model: SeedTrayModel): CatalogValues {
  return {
    identifier: model.identifier,
    description: model.description ?? '',
    x_size: model.x_size,
    y_size: model.y_size,
    height: model.height,
    cell_size_ml: model.cell_size_ml,
    x_cells: model.x_cells,
    y_cells: model.y_cells
  }
}

interface SeedTrayModelCorrectionDialogProps {
  model: SeedTrayModel
  onSaved: () => void
  onCancel: () => void
}

function SeedTrayModelCorrectionDialog({ model, onSaved, onCancel }: SeedTrayModelCorrectionDialogProps) {
  const original = trayModelValues(model)
  const [values, setValues] = React.useState<CatalogValues>(original)
  const update = (name: string, value: unknown) => setValues((current) => ({ ...current, [name]: value }))
  const updateNumber = (name: string) => (event: React.ChangeEvent<HTMLInputElement>) => update(name, parseInt(event.target.value, 10) || 0)
  return (
    <CorrectionDialog
      collection="/seedtrays/seedtraymodels/"
      source={{ pk: model.pk, label: model.identifier }}
      original={original}
      values={values}
      title={`Correct ${model.identifier}`}
      onSaved={() => onSaved()}
      onCancel={onCancel}
    >
      {(fieldErrors, replacing) => (
        <>
          <Form.Group className="mb-3" controlId="tray-model-correction-identifier">
            <Form.Label>Name</Form.Label>
            <Form.Control value={String(values.identifier)} isInvalid={'identifier' in fieldErrors} onChange={(event) => update('identifier', event.target.value)} />
            <Form.Control.Feedback type="invalid">{fieldErrors.identifier}</Form.Control.Feedback>
            {replacing && <Form.Text>The new grid saves a second model, so give it a name of its own — {model.identifier} keeps the one its trays were received under.</Form.Text>}
            <DuplicateWarning collection="/seedtrays/seedtraymodels/" field="identifier" name={String(values.identifier)} exclude={replacing ? undefined : model.pk} />
          </Form.Group>
          <Form.Group className="mb-3" controlId="tray-model-correction-description">
            <Form.Label>Description</Form.Label>
            <Form.Control
              as="textarea"
              value={String(values.description)}
              isInvalid={'description' in fieldErrors}
              onChange={(event) => update('description', event.target.value)}
            />
            <Form.Control.Feedback type="invalid">{fieldErrors.description}</Form.Control.Feedback>
          </Form.Group>
          <div className="row">
            {TRAY_MODEL_MEASUREMENTS.map((measurement) => (
              <Form.Group key={measurement.name} className="mb-3 col-6 col-md-3" controlId={`tray-model-correction-${measurement.name}`}>
                <Form.Label>{measurement.label}</Form.Label>
                <Form.Control type="number" value={String(values[measurement.name])} isInvalid={measurement.name in fieldErrors} onChange={updateNumber(measurement.name)} />
                <Form.Control.Feedback type="invalid">{fieldErrors[measurement.name]}</Form.Control.Feedback>
              </Form.Group>
            ))}
          </div>
          <div className="row">
            <Form.Group className="mb-3 col-6" controlId="tray-model-correction-x-cells">
              <Form.Label>Cells across</Form.Label>
              <Form.Control type="number" value={String(values.x_cells)} isInvalid={'x_cells' in fieldErrors} onChange={updateNumber('x_cells')} />
              <Form.Control.Feedback type="invalid">{fieldErrors.x_cells}</Form.Control.Feedback>
            </Form.Group>
            <Form.Group className="mb-3 col-6" controlId="tray-model-correction-y-cells">
              <Form.Label>Cells down</Form.Label>
              <Form.Control type="number" value={String(values.y_cells)} isInvalid={'y_cells' in fieldErrors} onChange={updateNumber('y_cells')} />
              <Form.Control.Feedback type="invalid">{fieldErrors.y_cells}</Form.Control.Feedback>
            </Form.Group>
          </div>
        </>
      )}
    </CorrectionDialog>
  )
}

function SeedTrayModelsTable() {
  const queryClient = useQueryClient()
  const [showAddRow, setShowAddRow] = React.useState(false)
  const [showRetired, setShowRetired] = React.useState(false)
  const [retireError, setRetireError] = React.useState<string | null>(null)
  const [correcting, setCorrecting] = React.useState<SeedTrayModel | null>(null)
  const [search, setSearch] = React.useState('')
  // The whole list names the model a row was replaced by, which a search for
  // the successor's identifier would otherwise leave the row pointing at
  // nothing; `found` is what the rows themselves are read from.
  const { data: seedTrayModels = [] } = useQuery({
    queryKey: queryKeys.seedTrays.models,
    queryFn: ({ signal }) => getSeedTrayModels(signal)
  })
  const { data: found = [] } = useQuery({
    queryKey: searchedKey(queryKeys.seedTrays.models, search),
    queryFn: ({ signal }) => getSeedTrayModels(signal, search)
  })
  // A correction renames the paired inventory item and a replacement creates a
  // second one, so the item lists go with the catalog rather than waiting for
  // whichever screen happens to be opened next.
  const invalidate = async () => {
    await queryClient.invalidateQueries({ queryKey: queryKeys.seedTrays.models })
    await queryClient.invalidateQueries({ queryKey: queryKeys.inventory.all })
  }
  const modelMutation = useMutation({
    mutationFn: addSeedTrayModel,
    onSuccess: invalidate
  })
  const retireMutation = useMutation({
    mutationFn: ({ pk, active }: { pk: number; active: boolean }) => updateSeedTrayModel(pk, { active }),
    onSuccess: invalidate
  })

  async function createModel(data: SeedTrayModelCreate) {
    await modelMutation.mutateAsync(data)
  }

  async function retire(pk: number, active: boolean) {
    setRetireError(null)
    try {
      await retireMutation.mutateAsync({ pk, active })
    } catch (error) {
      setRetireError(errorsByField(error).active ?? 'The tray model could not be changed.')
    }
  }

  return (
    <>
      {retireError && (
        <Alert variant="danger" onClose={() => setRetireError(null)} dismissible>
          {retireError}
        </Alert>
      )}
      {correcting && (
        <SeedTrayModelCorrectionDialog
          model={correcting}
          onSaved={() => {
            setCorrecting(null)
            invalidate()
          }}
          onCancel={() => setCorrecting(null)}
        />
      )}
      <CatalogSearch id="tray-model-search" onSearch={setSearch} label="Search tray models" />
      <Form.Check type="switch" id="show-retired-tray-models" label="Show retired" checked={showRetired} onChange={(event) => setShowRetired(event.target.checked)} />
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
            <th>Actions</th>
          </tr>
        </thead>
        <tbody>
          {showAddRow && <SeedTrayModelNew key="add" createModel={createModel} done={() => setShowAddRow(false)} />}
          {found
            .filter((model) => model.active || showRetired)
            .map((model) => (
              <tr key={model.pk} className={retiredRowClass(model.active)}>
                <td>{model.pk}</td>
                <td>
                  {model.identifier}
                  <RetiredBadge active={model.active} />
                  <ReplacedByNote by={seedTrayModels.find((candidate) => candidate.pk === model.replaced_by)?.identifier ?? null} />
                </td>
                <td>{model.description}</td>
                <td>
                  {model.x_size}x{model.y_size}x{model.height} ({model.x_cells}x{model.y_cells})
                </td>
                <td>{model.cell_size_ml}</td>
                <td>
                  {model.replaced_by === null && (
                    <>
                      <Button size="sm" variant="outline-secondary" onClick={() => setCorrecting(model)}>
                        Correct
                      </Button>{' '}
                    </>
                  )}
                  <RetireButton active={model.active} onChange={(active) => retire(model.pk, active)} />
                </td>
              </tr>
            ))}
        </tbody>
      </Table>
    </>
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
