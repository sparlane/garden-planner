import 'bootstrap'
import 'bootstrap/dist/css/bootstrap.css'
import './garden.css'

import React, { useMemo, useState } from 'react'
import type { KeyboardEvent } from 'react'
import { Button, Modal } from 'react-bootstrap'
import Select from 'react-select'
import { useQuery } from '@tanstack/react-query'
import { useNavigate, useParams } from 'react-router'

import { GardenArea, GardenBed, GardenSquare } from './types/garden'
import { GardenSquarePlanting } from './types/plantings'
import { getGardenAreas, getGardenBeds, getGardenSquares } from './api/garden'
import { getPlantingGardenSquaresCurrent } from './api/plantings'
import { SelectOption } from './types/others'
import { queryKeys } from './query'

const OUTLINE_WIDTH = 100

interface GardenAreaDisplayProps {
  area: GardenArea
  gardenBeds: Array<GardenBed>
  squares: Array<GardenSquare>
  plantings: Array<GardenSquarePlanting>
}

interface GardenSquareElementProps {
  area: GardenArea
  bed: GardenBed
  square: GardenSquare
  plantings: Array<GardenSquarePlanting>
  onSelect: (squarePk: number) => void
}

interface GardenBedElementProps {
  area: GardenArea
  bed: GardenBed
  squares: Array<GardenSquare>
  plantingsBySquare: Map<number, Array<GardenSquarePlanting>>
  onSelectSquare: (squarePk: number) => void
}

interface GardenSquareDetailsModalProps {
  area: GardenArea
  bed?: GardenBed
  square: GardenSquare
  plantings: Array<GardenSquarePlanting>
  onClose: () => void
}

function calculateSvgY(area: GardenArea, offsetY: number, placementY: number, sizeY: number): number {
  return OUTLINE_WIDTH + area.size_y - (offsetY + placementY + sizeY)
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

function GardenSquareElement({ area, bed, square, plantings, onSelect }: GardenSquareElementProps) {
  const description = squareDescription(square, plantings)
  const className = plantings.length > 0 ? 'garden-square garden-square--planted' : 'garden-square garden-square--empty'

  function handleKeyDown(event: KeyboardEvent<SVGGElement>) {
    if (event.key !== 'Enter' && event.key !== ' ') {
      return
    }

    event.preventDefault()
    onSelect(square.pk)
  }

  return (
    <g className="garden-square-control" role="button" tabIndex={0} aria-label={description} aria-haspopup="dialog" onClick={() => onSelect(square.pk)} onKeyDown={handleKeyDown}>
      <title>{description}</title>
      <rect
        className={className}
        x={OUTLINE_WIDTH + bed.placement_x + square.placement_x}
        y={calculateSvgY(area, bed.placement_y, square.placement_y, square.size_y)}
        width={square.size_x}
        height={square.size_y}
      />
    </g>
  )
}

function GardenBedElement({ area, bed, squares, plantingsBySquare, onSelectSquare }: GardenBedElementProps) {
  return (
    <g>
      <rect className="garden-bed" x={OUTLINE_WIDTH + bed.placement_x} y={calculateSvgY(area, 0, bed.placement_y, bed.size_y)} width={bed.size_x} height={bed.size_y}>
        <title>{bed.name}</title>
      </rect>
      {squares.map((square) => (
        <GardenSquareElement key={square.pk} area={area} bed={bed} square={square} plantings={plantingsBySquare.get(square.pk) ?? []} onSelect={onSelectSquare} />
      ))}
    </g>
  )
}

function GardenSquareDetailsModal({ area, bed, square, plantings, onClose }: GardenSquareDetailsModalProps) {
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
      </Modal.Body>
      <Modal.Footer>
        <Button variant="secondary" onClick={onClose}>
          Close
        </Button>
      </Modal.Footer>
    </Modal>
  )
}

function GardenAreaDisplay({ area, gardenBeds, squares, plantings }: GardenAreaDisplayProps) {
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
  const viewWidth = area.size_x + OUTLINE_WIDTH * 2
  const viewHeight = area.size_y + OUTLINE_WIDTH * 2
  const titleId = `garden-area-${area.pk}-title`

  return (
    <>
      <div className="garden-area-container">
        <svg className="garden-area-display" viewBox={`0 0 ${viewWidth} ${viewHeight}`} role="group" aria-labelledby={titleId}>
          <title id={titleId}>{area.name} garden layout</title>
          <rect className="garden-area-outline" x={OUTLINE_WIDTH / 2} y={OUTLINE_WIDTH / 2} width={area.size_x + OUTLINE_WIDTH} height={area.size_y + OUTLINE_WIDTH} />
          {gardenBeds.map((bed) => (
            <GardenBedElement
              key={bed.pk}
              area={area}
              bed={bed}
              squares={squares.filter((square) => square.bed === bed.pk)}
              plantingsBySquare={plantingsBySquare}
              onSelectSquare={setSelectedSquarePk}
            />
          ))}
        </svg>
      </div>
      {selectedSquare !== undefined && (
        <GardenSquareDetailsModal
          area={area}
          bed={selectedBed}
          square={selectedSquare}
          plantings={plantingsBySquare.get(selectedSquare.pk) ?? []}
          onClose={() => setSelectedSquarePk(undefined)}
        />
      )}
    </>
  )
}

function GardenDisplay() {
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
      areaView = <GardenAreaDisplay key={area.pk} area={area} gardenBeds={beds.filter((bed) => bed.area === area.pk)} squares={squares} plantings={plantings} />
    } else if (!areasPending) {
      areaView = <div>Garden area not found.</div>
    }
  }

  return (
    <>
      <Select onChange={updateSelectedGardenArea} options={areaOptions} value={areaOptions.find((option) => option.value === selectedArea)} />
      <div>{areaView}</div>
    </>
  )
}

export { GardenDisplay }
