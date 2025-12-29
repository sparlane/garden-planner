import 'bootstrap'
import 'bootstrap/dist/css/bootstrap.css'

import React from 'react'
import Select from 'react-select'

import $ from 'jquery'

import { GardenArea, GardenBed, GardenSquare, GardenRow } from './types/garden'
import { GardenSquarePlanting } from './types/plantings'
import { SelectOption } from './types/others'

interface GardenAreaDisplayProps {
  area: GardenArea
  gardenBeds: Array<GardenBed>
  squares: Array<GardenSquare>
  plantings: Array<GardenSquarePlanting>
}

class GardenAreaDisplay extends React.Component<GardenAreaDisplayProps> {
  canvasRef: React.RefObject<HTMLCanvasElement>
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
    ctx.scale(scale, scale)
    ctx.lineWidth = this.outlineWidth
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

interface GardenDisplayState {
  selectedArea?: number
  areas: Array<GardenArea>
  beds: Array<GardenBed>
  rows: Array<GardenRow>
  squares: Array<GardenSquare>
  plantings: Array<GardenSquarePlanting>
}

class GardenDisplay extends React.Component<undefined, GardenDisplayState> {
  timer?: number
  constructor(props: undefined) {
    super(props)

    this.state = {
      selectedArea: undefined,
      areas: [],
      beds: [],
      rows: [],
      squares: [],
      plantings: []
    }

    this.updateSelectedGardenArea = this.updateSelectedGardenArea.bind(this)
    this.updateGardenAreas = this.updateGardenAreas.bind(this)
    this.updateGardenBeds = this.updateGardenBeds.bind(this)
    this.updateGardenRows = this.updateGardenRows.bind(this)
    this.updateGardenSquares = this.updateGardenSquares.bind(this)
    this.updateGardenSquaresPlanting = this.updateGardenSquaresPlanting.bind(this)
  }

  componentDidMount() {
    this.updateData()
    this.timer = setInterval(() => this.updateData(), 10000)
  }

  componentWillUnmount() {
    clearInterval(this.timer)
    this.timer = undefined
  }

  updateSelectedGardenArea(selectedGardenArea: SelectOption | null) {
    const value = selectedGardenArea?.value

    if (value === undefined || value === null) {
      this.setState({ selectedArea: undefined })
    } else {
      this.setState({ selectedArea: Number(value) })
    }
  }

  updateGardenAreas(data: Array<GardenArea>) {
    this.setState({
      areas: data
    })
  }

  updateGardenBeds(data: Array<GardenBed>) {
    this.setState({
      beds: data
    })
  }

  updateGardenRows(data: Array<GardenRow>) {
    this.setState({
      rows: data
    })
  }

  updateGardenSquares(data: Array<GardenSquare>) {
    this.setState({
      squares: data
    })
  }

  updateGardenSquaresPlanting(data: { plantings: Array<GardenSquarePlanting> }) {
    this.setState({
      plantings: data.plantings
    })
  }

  async updateData() {
    await $.getJSON('/garden/areas/', this.updateGardenAreas)
    await $.getJSON('/garden/beds/', this.updateGardenBeds)
    await $.getJSON('/garden/rows/', this.updateGardenRows)
    await $.getJSON('/garden/squares/', this.updateGardenSquares)
    await $.getJSON('/plantings/garden/squares/current/', this.updateGardenSquaresPlanting)
  }

  render() {
    const areas = []
    for (const idx in this.state.areas) {
      const area = this.state.areas[idx]
      areas.push({ value: area.pk, label: area.name })
    }
    let areaView = undefined
    if (this.state.selectedArea !== undefined) {
      const area = this.state.areas.find((a) => a.pk === this.state.selectedArea)
      if (area) {
        const beds = this.state.beds.filter((b) => b.area === area?.pk)
        areaView = <GardenAreaDisplay key={area?.pk} area={area} gardenBeds={beds} squares={this.state.squares} plantings={this.state.plantings} />
      }
    }
    return (
      <>
        <Select onChange={this.updateSelectedGardenArea} options={areas} value={areas.find((o) => o.value === this.state.selectedArea)} />
        <div>{areaView}</div>
      </>
    )
  }
}

export { GardenDisplay }
