import React, { useState } from 'react'
import type { KeyboardEvent } from 'react'
import { Button, ButtonGroup } from 'react-bootstrap'

import { GardenBed, GardenRow, GardenSquare } from '../types/garden'

const OUTLINE_WIDTH = 100

interface CanvasArea {
  pk?: number
  name: string
  size_x: number
  size_y: number
}

interface GardenCanvasProps {
  area: CanvasArea
  beds: Array<GardenBed>
  rows?: Array<GardenRow>
  squares: Array<GardenSquare>
  describeBed?: (bed: GardenBed) => string
  describeRow?: (row: GardenRow) => string
  describeSquare?: (square: GardenSquare) => string
  bedClassName?: (bed: GardenBed) => string
  rowClassName?: (row: GardenRow) => string
  squareClassName?: (square: GardenSquare) => string
  onSelectBed?: (bedPk: number) => void
  onSelectRow?: (rowPk: number) => void
  onSelectSquare?: (squarePk: number) => void
}

interface GardenBedElementProps {
  area: CanvasArea
  bed: GardenBed
  rows: Array<GardenRow>
  squares: Array<GardenSquare>
  describeBed: (bed: GardenBed) => string
  describeRow: (row: GardenRow) => string
  describeSquare: (square: GardenSquare) => string
  bedClassName: (bed: GardenBed) => string
  rowClassName: (row: GardenRow) => string
  squareClassName: (square: GardenSquare) => string
  onSelectBed?: (bedPk: number) => void
  onSelectRow?: (rowPk: number) => void
  onSelectSquare?: (squarePk: number) => void
}

interface SelectableGeometryProps {
  children: React.ReactNode
  description: string
  geometryPk: number
  onSelect?: (pk: number) => void
}

function calculateSvgY(area: CanvasArea, offsetY: number, placementY: number, sizeY: number): number {
  return OUTLINE_WIDTH + area.size_y - (offsetY + placementY + sizeY)
}

function groupByParent<T extends { bed: number }>(children: Array<T>): Map<number, Array<T>> {
  const grouped = new Map<number, Array<T>>()
  for (const child of children) {
    const siblings = grouped.get(child.bed) ?? []
    siblings.push(child)
    grouped.set(child.bed, siblings)
  }
  return grouped
}

function SelectableGeometry({ children, description, geometryPk, onSelect }: SelectableGeometryProps) {
  if (onSelect === undefined) {
    return (
      <g>
        <title>{description}</title>
        {children}
      </g>
    )
  }

  function handleKeyDown(event: KeyboardEvent<SVGGElement>) {
    if (event.key !== 'Enter' && event.key !== ' ') return
    event.preventDefault()
    onSelect?.(geometryPk)
  }

  return (
    <g
      className="garden-geometry-control"
      role="button"
      tabIndex={0}
      aria-label={description}
      aria-haspopup="dialog"
      onClick={() => onSelect(geometryPk)}
      onKeyDown={handleKeyDown}
    >
      <title>{description}</title>
      {children}
    </g>
  )
}

function GardenBedElement({
  area,
  bed,
  rows,
  squares,
  describeBed,
  describeRow,
  describeSquare,
  bedClassName,
  rowClassName,
  squareClassName,
  onSelectBed,
  onSelectRow,
  onSelectSquare
}: GardenBedElementProps) {
  return (
    <g>
      <SelectableGeometry description={describeBed(bed)} geometryPk={bed.pk} onSelect={onSelectBed}>
        <rect className={bedClassName(bed)} x={OUTLINE_WIDTH + bed.placement_x} y={calculateSvgY(area, 0, bed.placement_y, bed.size_y)} width={bed.size_x} height={bed.size_y} />
      </SelectableGeometry>
      {rows.map((row) => (
        <SelectableGeometry key={row.pk} description={describeRow(row)} geometryPk={row.pk} onSelect={onSelectRow}>
          <rect
            className={rowClassName(row)}
            x={OUTLINE_WIDTH + bed.placement_x + row.placement_x}
            y={calculateSvgY(area, bed.placement_y, row.placement_y, row.size_y)}
            width={row.size_x}
            height={row.size_y}
          />
        </SelectableGeometry>
      ))}
      {squares.map((square) => (
        <SelectableGeometry key={square.pk} description={describeSquare(square)} geometryPk={square.pk} onSelect={onSelectSquare}>
          <rect
            className={squareClassName(square)}
            x={OUTLINE_WIDTH + bed.placement_x + square.placement_x}
            y={calculateSvgY(area, bed.placement_y, square.placement_y, square.size_y)}
            width={square.size_x}
            height={square.size_y}
          />
        </SelectableGeometry>
      ))}
    </g>
  )
}

function GardenCanvas({
  area,
  beds,
  rows = [],
  squares,
  describeBed = (bed) => `${bed.name}: ${bed.kind.replace('_', ' ')}`,
  describeRow = (row) => row.name,
  describeSquare = (square) => square.name,
  bedClassName = (bed) => `garden-bed garden-bed--${bed.kind}`,
  rowClassName = () => 'garden-row',
  squareClassName = () => 'garden-square garden-square--empty',
  onSelectBed,
  onSelectRow,
  onSelectSquare
}: GardenCanvasProps) {
  const [zoom, setZoom] = useState(1)
  const rowsByBed = React.useMemo(() => groupByParent(rows), [rows])
  const squaresByBed = React.useMemo(() => groupByParent(squares), [squares])
  const viewWidth = area.size_x + OUTLINE_WIDTH * 2
  const viewHeight = area.size_y + OUTLINE_WIDTH * 2
  const titleId = `garden-area-${area.pk ?? 'preview'}-title`

  return (
    <div>
      <div className="d-flex align-items-center justify-content-between gap-2 mb-2">
        <span className="small text-muted">Pinch, scroll, or use the controls to inspect the plan.</span>
        <ButtonGroup size="sm" aria-label="Garden map zoom">
          <Button variant="outline-secondary" disabled={zoom <= 0.75} aria-label="Zoom out" onClick={() => setZoom((value) => Math.max(0.75, value - 0.25))}>
            −
          </Button>
          <Button variant="outline-secondary" onClick={() => setZoom(1)} aria-label={`Reset zoom, currently ${Math.round(zoom * 100)} percent`}>
            {Math.round(zoom * 100)}%
          </Button>
          <Button variant="outline-secondary" disabled={zoom >= 3} aria-label="Zoom in" onClick={() => setZoom((value) => Math.min(3, value + 0.25))}>
            +
          </Button>
        </ButtonGroup>
      </div>
      <div className="garden-area-container">
        <svg className="garden-area-display" style={{ width: `${zoom * 100}%` }} viewBox={`0 0 ${viewWidth} ${viewHeight}`} role="group" aria-labelledby={titleId}>
          <title id={titleId}>{area.name} garden layout</title>
          <rect className="garden-area-outline" x={OUTLINE_WIDTH / 2} y={OUTLINE_WIDTH / 2} width={area.size_x + OUTLINE_WIDTH} height={area.size_y + OUTLINE_WIDTH} />
          <rect className="garden-unallocated" x={OUTLINE_WIDTH} y={OUTLINE_WIDTH} width={area.size_x} height={area.size_y}>
            <title>Paths and unallocated space</title>
          </rect>
          {beds.map((bed) => (
            <GardenBedElement
              key={bed.pk}
              area={area}
              bed={bed}
              rows={rowsByBed.get(bed.pk) ?? []}
              squares={squaresByBed.get(bed.pk) ?? []}
              describeBed={describeBed}
              describeRow={describeRow}
              describeSquare={describeSquare}
              bedClassName={bedClassName}
              rowClassName={rowClassName}
              squareClassName={squareClassName}
              onSelectBed={onSelectBed}
              onSelectRow={onSelectRow}
              onSelectSquare={onSelectSquare}
            />
          ))}
        </svg>
      </div>
    </div>
  )
}

export { CanvasArea, GardenCanvas, OUTLINE_WIDTH, calculateSvgY }
