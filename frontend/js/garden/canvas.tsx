import React from 'react'
import type { KeyboardEvent } from 'react'

import { GardenBed, GardenRow, GardenSquare } from '../types/garden'

// The drawn area sits inside a margin so its outline is not clipped by the
// viewBox, and every child is offset by the same amount.
const OUTLINE_WIDTH = 100

// What the canvas needs to know about an area to draw it. The Gardens screen
// passes a saved area; the setup wizard passes one it has not written yet, so
// this is deliberately narrower than GardenArea.
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
  // Absent when nothing is selectable, which is how the wizard previews a
  // layout that has no squares to open yet.
  describeSquare?: (square: GardenSquare) => string
  squareClassName?: (square: GardenSquare) => string
  onSelectSquare?: (squarePk: number) => void
}

interface GardenBedElementProps {
  area: CanvasArea
  bed: GardenBed
  rows: Array<GardenRow>
  squares: Array<GardenSquare>
  describeSquare: (square: GardenSquare) => string
  squareClassName: (square: GardenSquare) => string
  onSelectSquare?: (squarePk: number) => void
}

interface GardenSquareElementProps {
  area: CanvasArea
  bed: GardenBed
  square: GardenSquare
  description: string
  className: string
  onSelect?: (squarePk: number) => void
}

// Model coordinates grow upwards from the bottom left; SVG grows downwards
// from the top left, so every y is flipped through the area's height.
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

function GardenSquareElement({ area, bed, square, description, className, onSelect }: GardenSquareElementProps) {
  const x = OUTLINE_WIDTH + bed.placement_x + square.placement_x
  const y = calculateSvgY(area, bed.placement_y, square.placement_y, square.size_y)

  if (onSelect === undefined) {
    return (
      <g>
        <title>{description}</title>
        <rect className={className} x={x} y={y} width={square.size_x} height={square.size_y} />
      </g>
    )
  }

  function handleKeyDown(event: KeyboardEvent<SVGGElement>) {
    if (event.key !== 'Enter' && event.key !== ' ') {
      return
    }

    event.preventDefault()
    onSelect?.(square.pk)
  }

  return (
    <g className="garden-square-control" role="button" tabIndex={0} aria-label={description} aria-haspopup="dialog" onClick={() => onSelect(square.pk)} onKeyDown={handleKeyDown}>
      <title>{description}</title>
      <rect className={className} x={x} y={y} width={square.size_x} height={square.size_y} />
    </g>
  )
}

function GardenBedElement({ area, bed, rows, squares, describeSquare, squareClassName, onSelectSquare }: GardenBedElementProps) {
  return (
    <g>
      <rect className="garden-bed" x={OUTLINE_WIDTH + bed.placement_x} y={calculateSvgY(area, 0, bed.placement_y, bed.size_y)} width={bed.size_x} height={bed.size_y}>
        <title>{bed.name}</title>
      </rect>
      {rows.map((row) => (
        <rect
          key={row.pk}
          className="garden-row"
          x={OUTLINE_WIDTH + bed.placement_x + row.placement_x}
          y={calculateSvgY(area, bed.placement_y, row.placement_y, row.size_y)}
          width={row.size_x}
          height={row.size_y}
        >
          <title>{row.name}</title>
        </rect>
      ))}
      {squares.map((square) => (
        <GardenSquareElement
          key={square.pk}
          area={area}
          bed={bed}
          square={square}
          description={describeSquare(square)}
          className={squareClassName(square)}
          onSelect={onSelectSquare}
        />
      ))}
    </g>
  )
}

// The one place an area, its beds, its rows, and its squares are drawn. The
// Gardens screen and the setup preview both render through it so that what a
// gardener is shown before saving is what they get afterwards.
function GardenCanvas({
  area,
  beds,
  rows = [],
  squares,
  describeSquare = (square) => square.name,
  squareClassName = () => 'garden-square garden-square--empty',
  onSelectSquare
}: GardenCanvasProps) {
  const rowsByBed = React.useMemo(() => groupByParent(rows), [rows])
  const squaresByBed = React.useMemo(() => groupByParent(squares), [squares])
  const viewWidth = area.size_x + OUTLINE_WIDTH * 2
  const viewHeight = area.size_y + OUTLINE_WIDTH * 2
  const titleId = `garden-area-${area.pk ?? 'preview'}-title`

  return (
    <div className="garden-area-container">
      <svg className="garden-area-display" viewBox={`0 0 ${viewWidth} ${viewHeight}`} role="group" aria-labelledby={titleId}>
        <title id={titleId}>{area.name} garden layout</title>
        <rect className="garden-area-outline" x={OUTLINE_WIDTH / 2} y={OUTLINE_WIDTH / 2} width={area.size_x + OUTLINE_WIDTH} height={area.size_y + OUTLINE_WIDTH} />
        {beds.map((bed) => (
          <GardenBedElement
            key={bed.pk}
            area={area}
            bed={bed}
            rows={rowsByBed.get(bed.pk) ?? []}
            squares={squaresByBed.get(bed.pk) ?? []}
            describeSquare={describeSquare}
            squareClassName={squareClassName}
            onSelectSquare={onSelectSquare}
          />
        ))}
      </svg>
    </div>
  )
}

export { CanvasArea, GardenCanvas, OUTLINE_WIDTH, calculateSvgY }
