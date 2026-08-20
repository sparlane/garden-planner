import 'bootstrap'
import 'bootstrap/dist/css/bootstrap.css'
import './garden.css'

import React, { useMemo, useState } from 'react'
import { Alert, Button, Modal } from 'react-bootstrap'
import Select from 'react-select'
import { useQuery } from '@tanstack/react-query'
import { Link, useNavigate, useParams } from 'react-router'

import { GardenArea, GardenBed, GardenRow, GardenSquare } from './types/garden'
import { GardenSquarePlanting } from './types/plantings'
import { getGardenAreas, getGardenBeds, getGardenRows, getGardenSquares } from './api/garden'
import { getHarvests, getPlantingGardenSquaresCurrent } from './api/plantings'
import { HarvestForm, HarvestFormBatch, HarvestFormPlant } from './plantings/harvest_form'
import { InputApplicationForm } from './applications/application_form'
import { ConfirmGeometryForm } from './garden/geometry'
import { GardenCanvas } from './garden/canvas'
import { HarvestTable } from './plantings/harvest_list'
import { SelectOption } from './types/others'
import { Workspace } from './types/workspace'
import { queryKeys } from './query'

interface GardenAreaDisplayProps {
  area: GardenArea
  gardenBeds: Array<GardenBed>
  rows: Array<GardenRow>
  squares: Array<GardenSquare>
  plantings: Array<GardenSquarePlanting>
  workspace: Workspace
}

interface GardenSquareDetailsModalProps {
  area: GardenArea
  bed?: GardenBed
  square: GardenSquare
  plantings: Array<GardenSquarePlanting>
  onClose: () => void
  workspace: Workspace
}

function plantingName(planting: GardenSquarePlanting): string {
  return `${planting.plant} — ${planting.variety}`
}

function squareDescription(square: GardenSquare, plantings: Array<GardenSquarePlanting>): string {
  if (plantings.length === 0) {
    return `${square.name}: empty`
  }

  const plantingDescriptions = plantings.map((planting) => `${plantingName(planting)} (${planting.quantity})`)
  return `${square.name}: ${plantingDescriptions.join(', ')}`
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

function GardenSquareDetailsModal({ area, bed, square, plantings, onClose, workspace }: GardenSquareDetailsModalProps) {
  return (
    <Modal show onHide={onClose} size="lg" aria-labelledby="garden-square-details-title">
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
          <p className="mb-0">This square has no current plantings.</p>
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
              </section>
            )
          })
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
      </Modal.Body>
      <Modal.Footer>
        <Button variant="secondary" onClick={onClose}>
          Close
        </Button>
      </Modal.Footer>
    </Modal>
  )
}

function GardenAreaDisplay({ area, gardenBeds, rows, squares, plantings, workspace }: GardenAreaDisplayProps) {
  const [selectedSquarePk, setSelectedSquarePk] = useState<number>()
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
  const selectedBed = selectedSquare === undefined ? undefined : gardenBeds.find((bed) => bed.pk === selectedSquare.bed)

  return (
    <>
      <GardenCanvas
        area={area}
        beds={gardenBeds}
        rows={rows}
        squares={squares}
        describeSquare={(square) => squareDescription(square, plantingsBySquare.get(square.pk) ?? [])}
        squareClassName={(square) => ((plantingsBySquare.get(square.pk) ?? []).length > 0 ? 'garden-square garden-square--planted' : 'garden-square garden-square--empty')}
        onSelectSquare={setSelectedSquarePk}
      />
      {selectedSquare !== undefined && (
        <GardenSquareDetailsModal
          area={area}
          bed={selectedBed}
          square={selectedSquare}
          plantings={plantingsBySquare.get(selectedSquare.pk) ?? []}
          onClose={() => setSelectedSquarePk(undefined)}
          workspace={workspace}
        />
      )}
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
      areaView = (
        <GardenAreaDisplay
          key={area.pk}
          area={area}
          gardenBeds={areaBeds}
          rows={rows.filter((row) => bedPks.has(row.bed))}
          squares={squares}
          plantings={plantings}
          workspace={workspace}
        />
      )
    } else if (!areasPending) {
      areaView = <div>Garden area not found.</div>
    }
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
