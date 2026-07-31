import 'bootstrap'
import 'bootstrap/dist/css/bootstrap.css'

import React from 'react'
import Select from 'react-select'
import { useQuery } from '@tanstack/react-query'
import { useNavigate, useParams } from 'react-router'

import { GardenArea, GardenBed, GardenSquare } from './types/garden'
import { GardenSquarePlanting } from './types/plantings'
import { getGardenAreas, getGardenBeds, getGardenSquares } from './api/garden'
import { getPlantingGardenSquaresCurrent } from './api/plantings'
import { SelectOption } from './types/others'
import { queryKeys } from './query'

interface GardenAreaDisplayProps {
  area: GardenArea
  gardenBeds: Array<GardenBed>
  squares: Array<GardenSquare>
  plantings: Array<GardenSquarePlanting>
}

function haveSameItems<T>(previousItems: Array<T>, currentItems: Array<T>): boolean {
  return previousItems.length === currentItems.length && previousItems.every((item, index) => item === currentItems[index])
}

class GardenAreaDisplay extends React.Component<GardenAreaDisplayProps> {
  canvasRef: React.RefObject<HTMLCanvasElement | null>
  outlineWidth: number

  constructor(props: GardenAreaDisplayProps) {
    super(props)
    this.canvasRef = React.createRef()
    this.outlineWidth = 100
  }

  calculateX(offsetX: number, X: number): number {
    return this.outlineWidth + offsetX + X
  }

  calculateY(offsetY: number, Y: number): number {
    return this.outlineWidth + (this.props.area.size_y - (offsetY + Y))
  }

  drawBox(ctx: CanvasRenderingContext2D, line_width: number, offsetX: number, offsetY: number, startX: number, startY: number, sizeX: number, sizeY: number) {
    ctx.lineWidth = line_width
    const halfLineWidth = line_width / 2
    // goto the bottom left (lowest x, y)
    ctx.moveTo(this.calculateX(offsetX, startX - halfLineWidth), this.calculateY(offsetY, startY - halfLineWidth))
    // draw the left line towards the top left (lowest x, highest y)
    ctx.lineTo(this.calculateX(offsetX, startX - halfLineWidth), this.calculateY(offsetY, startY + sizeY + halfLineWidth))
    // draw the top line towards the top right (highest x, highest y)
    ctx.lineTo(this.calculateX(offsetX, startX + sizeX + halfLineWidth), this.calculateY(offsetY, startY + sizeY + halfLineWidth))
    // draw the right line towards the bottom right (highest x, lowest y)
    ctx.lineTo(this.calculateX(offsetX, startX + sizeX + halfLineWidth), this.calculateY(offsetY, startY - halfLineWidth))
    // draw the bottom line towards the bottom left (lowest x, lowest y)
    ctx.lineTo(this.calculateX(offsetX, startX - halfLineWidth), this.calculateY(offsetY, startY - halfLineWidth))
    ctx.stroke()
  }

  fillBox(ctx: CanvasRenderingContext2D, offsetX: number, offsetY: number, startX: number, startY: number, sizeX: number, sizeY: number) {
    ctx.fillRect(this.calculateX(offsetX, startX), this.calculateY(offsetY, startY + sizeY), sizeX, sizeY)
  }

  drawSquare(ctx: CanvasRenderingContext2D, bed: GardenBed, square: GardenSquare) {
    ctx.beginPath()
    ctx.strokeStyle = 'lightblue'
    this.drawBox(ctx, 10, bed.placement_x, bed.placement_y, square.placement_x, square.placement_y, square.size_x, square.size_y)
    const planting = this.props.plantings.find((p) => p.location.pk === square.pk)
    if (planting) {
      ctx.fillStyle = 'lightgreen'
      this.fillBox(ctx, bed.placement_x, bed.placement_y, square.placement_x, square.placement_y, square.size_x, square.size_y)
    }
  }

  drawBed(ctx: CanvasRenderingContext2D, bed: GardenBed) {
    ctx.beginPath()
    ctx.strokeStyle = 'grey'
    this.drawBox(ctx, 50, 0, 0, bed.placement_x, bed.placement_y, bed.size_x, bed.size_y)
    const squares = this.props.squares.filter((s) => s.bed === bed.pk)
    for (const idx in squares) {
      const square = squares[idx]
      this.drawSquare(ctx, bed, square)
    }
  }

  drawGarden(canvas: HTMLCanvasElement) {
    const { area } = this.props
    const ctx = canvas.getContext('2d')
    const scaleX = canvas.width / (area.size_x + this.outlineWidth * 2)
    const scaleY = canvas.height / (area.size_y + this.outlineWidth * 2)
    let scale = scaleX
    if (scaleY < scaleX) {
      scale = scaleY
    }
    if (ctx === null) {
      return
    }
    ctx.setTransform(1, 0, 0, 1, 0, 0)
    ctx.clearRect(0, 0, canvas.width, canvas.height)
    ctx.scale(scale, scale)
    ctx.lineWidth = this.outlineWidth
    ctx.strokeStyle = 'black'
    ctx.beginPath()
    ctx.moveTo(0 + this.outlineWidth / 2, 0 + this.outlineWidth / 2)
    ctx.lineTo(0 + this.outlineWidth / 2, area.size_y + this.outlineWidth * 1.5)
    ctx.lineTo(area.size_x + this.outlineWidth * 1.5, area.size_y + this.outlineWidth * 1.5)
    ctx.lineTo(area.size_x + this.outlineWidth * 1.5, 0 + this.outlineWidth / 2)
    ctx.lineTo(0 + this.outlineWidth / 2, 0 + this.outlineWidth / 2)
    ctx.stroke()
    for (const idx in this.props.gardenBeds) {
      const gardenBed = this.props.gardenBeds[idx]
      this.drawBed(ctx, gardenBed)
    }
  }

  componentDidMount() {
    this.redrawGarden()
  }

  componentDidUpdate(previousProps: GardenAreaDisplayProps) {
    if (
      previousProps.area === this.props.area &&
      haveSameItems(previousProps.gardenBeds, this.props.gardenBeds) &&
      haveSameItems(previousProps.squares, this.props.squares) &&
      haveSameItems(previousProps.plantings, this.props.plantings)
    ) {
      return
    }
    this.redrawGarden()
  }

  redrawGarden() {
    const canvas = this.canvasRef.current
    if (canvas === null) {
      return
    }
    this.drawGarden(canvas)
  }

  render() {
    return <canvas ref={this.canvasRef} width={(this.props.area.size_x + this.outlineWidth * 2) / 10} height={(this.props.area.size_y + this.outlineWidth * 2) / 10} />
  }
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
