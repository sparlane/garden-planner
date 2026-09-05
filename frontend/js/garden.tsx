import 'bootstrap'
import 'bootstrap/dist/css/bootstrap.css'
import './garden.css'

import React, { useEffect, useMemo, useState } from 'react'
import { Alert, Button, Card, Modal } from 'react-bootstrap'
import Select from 'react-select'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Link, useNavigate, useParams } from 'react-router'

import { GardenArea, GardenBed, GardenRow, GardenSquare } from './types/garden'
import { GardenRegisterRow, GardenSquarePlanting, PlantLifecycleState, PlantOutcomeAction } from './types/plantings'
import { getGardenAreas, getGardenBeds, getGardenRows, getGardenSquares } from './api/garden'
import { getGardenRegister, getHarvests, getPlantingGardenSquaresCurrent } from './api/plantings'
import { HarvestForm, HarvestFormBatch, HarvestFormPlant } from './plantings/harvest_form'
import { InputApplicationForm } from './applications/application_form'
import { ConfirmGeometryForm } from './garden/geometry'
import { GardenWorkspace, LayoutEditModal } from './garden/workspace'
import { HarvestTable } from './plantings/harvest_list'
import { SelectOption } from './types/others'
import { Workspace } from './types/workspace'
import { queryKeys } from './query'
import { GardenQuickAddButton } from './plantings/garden_quick_add'
import { GARDEN_OUTCOME_ACTIONS, PlantOutcomeButtons, PlantOutcomeDialog } from './plantings/lifecycle'

interface GardenAreaDisplayProps {
  area: GardenArea
  gardenBeds: Array<GardenBed>
  rows: Array<GardenRow>
  squares: Array<GardenSquare>
  plantings: Array<GardenSquarePlanting>
  registerRows: Array<GardenRegisterRow>
  workspace: Workspace
}

interface GardenSquareDetailsModalProps {
  area: GardenArea
  bed?: GardenBed
  square: GardenSquare
  plantings: Array<GardenSquarePlanting>
  onClose: () => void
  onEdit: () => void
  workspace: Workspace
}

function plantingName(planting: GardenSquarePlanting): string {
  return `${planting.plant} — ${planting.variety}`
}

function formatDateRange(early?: string, late?: string): string | undefined {
  if (early === undefined) {
    return late
  }
  if (late === undefined || early === late) {
    return early
  }
  return `${early} to ${late}`
}

// Everything a harvest form needs about this square is already in the current
// plantings payload: it carries one entry per direct sowing and one per
// individual plant standing here, each naming the batch that raised it.
function squareBatches(plantings: Array<GardenSquarePlanting>): Array<HarvestFormBatch> {
  const byPk = new Map(plantings.map((planting) => [planting.batch, `${planting.batch_code} · ${plantingName(planting)}`]))
  return [...byPk].map(([pk, label]) => ({ pk, label }))
}

function squarePlants(plantings: Array<GardenSquarePlanting>): Array<HarvestFormPlant> {
  return plantings
    .filter((planting) => planting.specific_plant_pk !== undefined)
    .map((planting) => ({
      pk: planting.specific_plant_pk as number,
      label: `Plant ${planting.specific_plant_pk} (${plantingName(planting)})`,
      batch: planting.batch,
      since: planting.transplanted ?? null
    }))
}

function SquareHarvests({ squarePk }: { squarePk: number }) {
  const { data: harvests = [] } = useQuery({
    queryKey: queryKeys.plantings.harvests('', '', squarePk, '', '', '', ''),
    queryFn: ({ signal }) => getHarvests({ garden_square: squarePk }, signal)
  })
  return <HarvestTable harvests={harvests} showLocation={false} />
}

function GardenSquareDetailsModal({ area, bed, square, plantings, onClose, onEdit, workspace }: GardenSquareDetailsModalProps) {
  const queryClient = useQueryClient()
  const [selectedOutcome, setSelectedOutcome] = useState<{ plant: { pk: number; lifecycle_state: PlantLifecycleState }; outcome: PlantOutcomeAction }>()

  function refreshOutcomeData(plantPk: number) {
    return Promise.all([
      queryClient.invalidateQueries({ queryKey: queryKeys.plantings.currentGardenSquares }),
      queryClient.invalidateQueries({ queryKey: queryKeys.plantings.specificPlantsAll }),
      queryClient.invalidateQueries({ queryKey: queryKeys.plantings.plantLifecycle(plantPk) }),
      queryClient.invalidateQueries({ queryKey: queryKeys.plantings.specificPlantDetail(plantPk) }),
      queryClient.invalidateQueries({ queryKey: queryKeys.plantings.gardenRegisterAll })
    ])
  }

  return (
    <>
      <Modal show={selectedOutcome === undefined} onHide={onClose} size="lg" aria-labelledby="garden-square-details-title">
        <Modal.Header closeButton>
          <Modal.Title id="garden-square-details-title">{square.name}</Modal.Title>
        </Modal.Header>
        <Modal.Body>
          <dl className="row garden-square-metadata">
            <dt className="col-sm-3">Garden area</dt>
            <dd className="col-sm-9">{area.name}</dd>
            <dt className="col-sm-3">Garden bed</dt>
            <dd className="col-sm-9">{bed?.name ?? 'Unknown bed'}</dd>
            <dt className="col-sm-3">Position</dt>
            <dd className="col-sm-9">
              {square.placement_x}, {square.placement_y}
            </dd>
            <dt className="col-sm-3">Size</dt>
            <dd className="col-sm-9">
              {square.size_x} × {square.size_y}
            </dd>
            <dt className="col-sm-3">Status</dt>
            <dd className="col-sm-9">{plantings.length > 0 ? 'Planted' : 'Empty'}</dd>
          </dl>

          {plantings.length === 0 ? (
            <p>This square has no current plantings.</p>
          ) : (
            plantings.map((planting, index) => {
              const germinationDates = formatDateRange(planting.germination_date_early, planting.germination_date_late)
              const maturityDates = formatDateRange(planting.maturity_date_early, planting.maturity_date_late)

              return (
                <section
                  className="garden-planting-detail"
                  key={`${planting.planting_pk}-${planting.transplanting_pk ?? 'direct'}-${planting.specific_plant_pk ?? 'aggregate'}-${index}`}
                >
                  <h2 className="h5">{plantingName(planting)}</h2>
                  <dl className="row mb-0">
                    <dt className="col-sm-3">Quantity</dt>
                    <dd className="col-sm-9">{planting.quantity}</dd>
                    <dt className="col-sm-3">Planted</dt>
                    <dd className="col-sm-9">{planting.planted}</dd>
                    {planting.transplanted !== undefined && (
                      <>
                        <dt className="col-sm-3">Transplanted</dt>
                        <dd className="col-sm-9">{planting.transplanted}</dd>
                      </>
                    )}
                    {germinationDates !== undefined && (
                      <>
                        <dt className="col-sm-3">Expected germination</dt>
                        <dd className="col-sm-9">{germinationDates}</dd>
                      </>
                    )}
                    {maturityDates !== undefined && (
                      <>
                        <dt className="col-sm-3">Expected maturity</dt>
                        <dd className="col-sm-9">{maturityDates}</dd>
                      </>
                    )}
                    {planting.notes && (
                      <>
                        <dt className="col-sm-3">Notes</dt>
                        <dd className="col-sm-9">{planting.notes}</dd>
                      </>
                    )}
                  </dl>
                  {planting.specific_plant_pk !== undefined && planting.lifecycle_state !== undefined && (
                    <div className="d-flex flex-wrap align-items-center gap-2 mt-2">
                      <Link to={`/plantings/plants/${planting.specific_plant_pk}`}>Open plant #{planting.specific_plant_pk}</Link>
                      <PlantOutcomeButtons
                        plant={{ pk: planting.specific_plant_pk, lifecycle_state: planting.lifecycle_state }}
                        actions={GARDEN_OUTCOME_ACTIONS}
                        disabled={selectedOutcome !== undefined}
                        onOutcome={(plant, outcome) => setSelectedOutcome({ plant, outcome })}
                      />
                    </div>
                  )}
                </section>
              )
            })
          )}

          {workspace.mode === 'garden' && (
            <div className="my-3">
              <GardenQuickAddButton initialSquare={square.pk} label="Add a planting here" />
            </div>
          )}

          <section className="garden-square-harvest">
            <h2 className="h5">Record a harvest</h2>
            <HarvestForm batches={squareBatches(plantings)} plants={squarePlants(plantings)} gardenSquare={square.pk} workspace={workspace} />
          </section>

          <section className="garden-square-harvest">
            <h2 className="h5">Harvests from this square</h2>
            <SquareHarvests squarePk={square.pk} />
          </section>

          <section className="garden-square-harvest">
            <h2 className="h5">Apply an input here</h2>
            {!area.geometry_confirmed && <ConfirmGeometryForm area={area} />}
            <InputApplicationForm
              targets={[
                {
                  key: `garden_square:${square.pk}`,
                  target_type: 'garden_square',
                  pk: square.pk,
                  label: square.name,
                  blocked: area.geometry_confirmed ? undefined : `${area.name} has no confirmed length unit`
                }
              ]}
              defaultTargetKeys={[`garden_square:${square.pk}`]}
              title="Apply an input to this square"
            />
          </section>
          <nav className="d-flex flex-wrap gap-3 mt-3" aria-label="More square actions">
            <Link to={`/plantings/garden-register?location=square:${square.pk}`}>View history</Link>
            <Link to="/health">Report a problem</Link>
            {plantings.some((planting) => planting.specific_plant_pk !== undefined) && <span className="text-muted">Open an individual plant above to move it.</span>}
          </nav>
        </Modal.Body>
        <Modal.Footer>
          <Button
            variant="outline-primary"
            onClick={() => {
              onClose()
              onEdit()
            }}
          >
            Edit layout
          </Button>
          <Button variant="secondary" onClick={onClose}>
            Close
          </Button>
        </Modal.Footer>
      </Modal>
      <PlantOutcomeDialog
        plant={selectedOutcome?.plant}
        outcome={selectedOutcome?.outcome}
        onClose={() => setSelectedOutcome(undefined)}
        onRecorded={() => (selectedOutcome === undefined ? undefined : refreshOutcomeData(selectedOutcome.plant.pk))}
      />
    </>
  )
}

interface GardenGeometryDetailsProps {
  area: GardenArea
  bed: GardenBed
  row?: GardenRow
  records: Array<GardenRegisterRow>
  workspace: Workspace
  onClose: () => void
  onEdit: () => void
}

function GardenGeometryDetailsModal({ area, bed, row, records, workspace, onClose, onEdit }: GardenGeometryDetailsProps) {
  const geometry = row ?? bed
  const kind = row === undefined ? (bed.kind === 'container' ? 'Container' : 'Bed') : 'Row'
  const batches = [...new Map(records.map((record) => [record.batch, `${record.batch_code} · ${record.plant_name} — ${record.variety_name}`])).entries()].map(([pk, label]) => ({
    pk,
    label
  }))
  const targetType = row === undefined ? 'garden_bed' : 'garden_row'

  return (
    <Modal show onHide={onClose} size="lg" aria-labelledby="garden-geometry-details-title">
      <Modal.Header closeButton>
        <Modal.Title id="garden-geometry-details-title">{geometry.name}</Modal.Title>
      </Modal.Header>
      <Modal.Body>
        <dl className="row">
          <dt className="col-sm-3">Type</dt>
          <dd className="col-sm-9">{kind}</dd>
          <dt className="col-sm-3">Garden area</dt>
          <dd className="col-sm-9">{area.name}</dd>
          {row !== undefined && (
            <>
              <dt className="col-sm-3">Garden bed</dt>
              <dd className="col-sm-9">{bed.name}</dd>
            </>
          )}
          <dt className="col-sm-3">Position</dt>
          <dd className="col-sm-9">
            {geometry.placement_x}, {geometry.placement_y}
          </dd>
          <dt className="col-sm-3">Size</dt>
          <dd className="col-sm-9">
            {geometry.size_x} × {geometry.size_y}
          </dd>
          <dt className="col-sm-3">Current crops</dt>
          <dd className="col-sm-9">{records.length === 0 ? 'None' : records.map((record) => `${record.plant_name} — ${record.variety_name} (${record.quantity})`).join(', ')}</dd>
        </dl>
        {row !== undefined && (
          <section className="mb-4">
            <h2 className="h5">Record a harvest</h2>
            <HarvestForm batches={batches} gardenRow={row.pk} workspace={workspace} />
          </section>
        )}
        <section>
          <h2 className="h5">Record care</h2>
          {!area.geometry_confirmed && <ConfirmGeometryForm area={area} />}
          <InputApplicationForm
            targets={[
              {
                key: `${targetType}:${geometry.pk}`,
                target_type: targetType,
                pk: geometry.pk,
                label: geometry.name,
                blocked: area.geometry_confirmed ? undefined : `${area.name} has no confirmed length unit`
              }
            ]}
            defaultTargetKeys={[`${targetType}:${geometry.pk}`]}
            title={`Apply an input to this ${kind.toLocaleLowerCase()}`}
          />
        </section>
        <nav className="d-flex flex-wrap gap-3 mt-3" aria-label={`More ${kind.toLocaleLowerCase()} actions`}>
          <Link to={`/plantings/garden-register?location=${row === undefined ? 'square' : 'row'}:${geometry.pk}`}>View history</Link>
          <Link to="/health">Report a problem</Link>
          {row !== undefined && <Link to="/plantings/garden-squares">Plant or move crops</Link>}
        </nav>
      </Modal.Body>
      <Modal.Footer>
        <Button
          variant="outline-primary"
          onClick={() => {
            onClose()
            onEdit()
          }}
        >
          Edit layout
        </Button>
        <Button variant="secondary" onClick={onClose}>
          Close
        </Button>
      </Modal.Footer>
    </Modal>
  )
}

function GardenAreaDisplay({ area, gardenBeds, rows, squares, plantings, registerRows, workspace }: GardenAreaDisplayProps) {
  const [selectedBedPk, setSelectedBedPk] = useState<number>()
  const [selectedRowPk, setSelectedRowPk] = useState<number>()
  const [selectedSquarePk, setSelectedSquarePk] = useState<number>()
  const [editing, setEditing] = useState<{ resource: 'areas' | 'beds' | 'rows' | 'squares'; geometry: GardenArea | GardenBed | GardenRow | GardenSquare }>()
  const plantingsBySquare = useMemo(() => {
    const groupedPlantings = new Map<number, Array<GardenSquarePlanting>>()
    for (const planting of plantings) {
      const squarePlantings = groupedPlantings.get(planting.location.pk) ?? []
      squarePlantings.push(planting)
      groupedPlantings.set(planting.location.pk, squarePlantings)
    }
    return groupedPlantings
  }, [plantings])
  const selectedSquare = squares.find((square) => square.pk === selectedSquarePk)
  const selectedRow = rows.find((row) => row.pk === selectedRowPk)
  const selectedBed = gardenBeds.find((bed) => bed.pk === (selectedSquare?.bed ?? selectedRow?.bed ?? selectedBedPk))
  const geometryRecords = registerRows.filter((record) => {
    if (selectedRow !== undefined) return record.location === `row:${selectedRow.pk}`
    if (selectedBed === undefined) return false
    const bedSquarePks = new Set(squares.filter((square) => square.bed === selectedBed.pk).map((square) => square.pk))
    const bedRowPks = new Set(rows.filter((row) => row.bed === selectedBed.pk).map((row) => row.pk))
    const [kind, identifier] = record.location.split(':')
    return (kind === 'square' && bedSquarePks.has(Number(identifier))) || (kind === 'row' && bedRowPks.has(Number(identifier)))
  })

  return (
    <>
      <div className="d-flex justify-content-between align-items-center">
        <h1 className="h3 mb-0">{area.name}</h1>
        <Button variant="outline-secondary" onClick={() => setEditing({ resource: 'areas', geometry: area })}>
          Edit area layout
        </Button>
      </div>
      <GardenWorkspace
        area={area}
        beds={gardenBeds}
        rows={rows}
        squares={squares}
        plantings={plantings}
        registerRows={registerRows}
        onSelectBed={setSelectedBedPk}
        onSelectRow={setSelectedRowPk}
        onSelectSquare={setSelectedSquarePk}
      />
      {selectedSquare !== undefined && (
        <GardenSquareDetailsModal
          area={area}
          bed={selectedBed}
          square={selectedSquare}
          plantings={plantingsBySquare.get(selectedSquare.pk) ?? []}
          onClose={() => setSelectedSquarePk(undefined)}
          onEdit={() => setEditing({ resource: 'squares', geometry: selectedSquare })}
          workspace={workspace}
        />
      )}
      {selectedSquare === undefined && selectedBed !== undefined && (
        <GardenGeometryDetailsModal
          area={area}
          bed={selectedBed}
          row={selectedRow}
          records={geometryRecords}
          workspace={workspace}
          onClose={() => {
            setSelectedBedPk(undefined)
            setSelectedRowPk(undefined)
          }}
          onEdit={() => setEditing({ resource: selectedRow === undefined ? 'beds' : 'rows', geometry: selectedRow ?? selectedBed })}
        />
      )}
      {editing !== undefined && <LayoutEditModal resource={editing.resource} geometry={editing.geometry} onClose={() => setEditing(undefined)} />}
    </>
  )
}

function GardenDisplay({ workspace }: { workspace: Workspace }) {
  const navigate = useNavigate()
  const { areaId } = useParams()
  const selectedArea = areaId === undefined ? undefined : Number(areaId)
  const { data: areas = [], isPending: areasPending } = useQuery({
    queryKey: queryKeys.garden.areas,
    queryFn: ({ signal }) => getGardenAreas(signal)
  })
  const { data: beds = [] } = useQuery({
    queryKey: queryKeys.garden.beds,
    queryFn: ({ signal }) => getGardenBeds(signal)
  })
  const { data: rows = [] } = useQuery({
    queryKey: queryKeys.garden.rows,
    queryFn: ({ signal }) => getGardenRows(signal)
  })
  const { data: squares = [] } = useQuery({
    queryKey: queryKeys.garden.squares,
    queryFn: ({ signal }) => getGardenSquares(signal)
  })
  const { data: plantings = [] } = useQuery({
    queryKey: queryKeys.plantings.currentGardenSquares,
    queryFn: ({ signal }) => getPlantingGardenSquaresCurrent(signal)
  })
  const { data: register } = useQuery({
    queryKey: ['plantings', 'garden-register', 'garden-workspace'],
    queryFn: ({ signal }) => getGardenRegister({ state: 'current', page_size: 100 }, signal),
    enabled: workspace.mode === 'garden'
  })
  const lastAreaKey = `garden-workspace:last-area:${workspace.name}`

  useEffect(() => {
    if (areasPending || areas.length === 0) return
    if (areaId !== undefined && areas.some((area) => area.pk === selectedArea)) {
      globalThis.localStorage.setItem(lastAreaKey, String(selectedArea))
      return
    }
    if (areaId !== undefined) return
    const remembered = Number(globalThis.localStorage.getItem(lastAreaKey))
    const destination = areas.find((area) => area.pk === remembered) ?? (areas.length === 1 ? areas[0] : undefined)
    if (destination !== undefined) navigate(`/gardens/${destination.pk}`, { replace: true })
  }, [areaId, areas, areasPending, lastAreaKey, navigate, selectedArea])

  function updateSelectedGardenArea(selectedGardenArea: SelectOption | null) {
    const value = selectedGardenArea?.value

    if (value === undefined || value === null) {
      navigate('/gardens')
    } else {
      navigate(`/gardens/${Number(value)}`)
    }
  }

  const areaOptions = areas.map((area) => ({ value: area.pk, label: area.name }))
  let areaView
  if (areaId !== undefined) {
    const area = areas.find((candidate) => candidate.pk === selectedArea)
    if (area) {
      const areaBeds = beds.filter((bed) => bed.area === area.pk)
      const bedPks = new Set(areaBeds.map((bed) => bed.pk))
      const areaSquares = squares.filter((square) => bedPks.has(square.bed))
      const areaSquarePks = new Set(areaSquares.map((square) => square.pk))
      areaView = (
        <GardenAreaDisplay
          key={area.pk}
          area={area}
          gardenBeds={areaBeds}
          rows={rows.filter((row) => bedPks.has(row.bed))}
          squares={areaSquares}
          plantings={plantings.filter((planting) => areaSquarePks.has(planting.location.pk))}
          registerRows={(register?.results ?? []).filter((record) => {
            const [kind, identifier] = record.location.split(':')
            if (kind === 'square') return areaSquarePks.has(Number(identifier))
            return kind === 'row' && rows.some((row) => row.pk === Number(identifier) && bedPks.has(row.bed))
          })}
          workspace={workspace}
        />
      )
    } else if (!areasPending) {
      areaView = <div>Garden area not found.</div>
    }
  } else if (!areasPending && areas.length > 1) {
    areaView = (
      <section className="mt-3" aria-labelledby="garden-overview-title">
        <h1 id="garden-overview-title" className="h3">
          Garden overview
        </h1>
        <div className="row g-3">
          {areas.map((area) => {
            const areaBeds = beds.filter((bed) => bed.area === area.pk)
            const bedPks = new Set(areaBeds.map((bed) => bed.pk))
            const areaSquares = squares.filter((square) => bedPks.has(square.bed))
            const occupied = new Set(plantings.map((planting) => planting.location.pk))
            return (
              <div className="col-md-6 col-xl-4" key={area.pk}>
                <Card className="h-100">
                  <Card.Body>
                    <Card.Title>{area.name}</Card.Title>
                    <Card.Text>
                      {areaBeds.length} beds and containers · {areaSquares.length} squares · {areaSquares.filter((square) => occupied.has(square.pk)).length} occupied
                    </Card.Text>
                    <Button onClick={() => navigate(`/gardens/${area.pk}`)}>Open workspace</Button>
                  </Card.Body>
                </Card>
              </div>
            )
          })}
        </div>
      </section>
    )
  }

  // A workspace with no garden yet has nothing to select between, and the bare
  // empty picker that used to stand here said so to nobody. Setup is the only
  // way to create an area, so it is what this screen offers instead.
  if (areas.length === 0 && !areasPending) {
    return (
      <Alert variant="info">
        <Alert.Heading>There is no garden here yet</Alert.Heading>
        <p>Set up a garden to lay out an area, its beds, and where things are kept. It takes a few minutes and you can leave it and come back.</p>
        <Link className="btn btn-primary" to="/setup">
          Set up my garden
        </Link>
      </Alert>
    )
  }

  return (
    <>
      <Select onChange={updateSelectedGardenArea} options={areaOptions} value={areaOptions.find((option) => option.value === selectedArea)} />
      <div>{areaView}</div>
      <p className="mt-3">
        <Link to="/setup">Add another garden area</Link>
      </p>
    </>
  )
}

export { GardenDisplay }
