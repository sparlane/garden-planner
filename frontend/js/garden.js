import 'bootstrap'
import 'bootstrap/dist/css/bootstrap.css'

import React from 'react'
import PropTypes from 'prop-types'

import $ from 'jquery'


class GardenAreaDisplay extends React.Component {
  constructor (props) {
    super(props)
    this.canvasRef = React.createRef()
    this.outlineWidth = 100
  }

  calculateX (offsetX, X) {
    return this.outlineWidth + offsetX + X
  }

  calculateY (offsetY, Y) {
    return this.outlineWidth + (this.props.area.size_y - (offsetY + Y))
  }

  drawBox (ctx, line_width, offsetX, offsetY, startX, startY, sizeX, sizeY) {
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

  drawBed (ctx, bed) {
    ctx.beginPath()
    ctx.strokeStyle = 'grey'
    this.drawBox(ctx, 50, 0, 0, bed.placement_x, bed.placement_y, bed.size_x, bed.size_y)
    const squares = this.props.squares.filter((s) => s.bed === bed.pk)
    for (const idx in squares) {
      const square = squares[idx]
      ctx.beginPath()
      ctx.strokeStyle = 'lightblue'
      this.drawBox(ctx, 10, bed.placement_x, bed.placement_y, square.placement_x, square.placement_y, square.size_x, square.size_y)
    }
  }

  drawGarden (canvas) {
    const area = this.props.area
    const ctx = canvas.getContext('2d')
    const scaleX = canvas.width / (area.size_x + (this.outlineWidth * 2))
    const scaleY = canvas.height / (area.size_y + (this.outlineWidth * 2))
    let scale = scaleX
    if (scaleY < scaleX) {
      scale = scaleY
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

  componentDidMount () {
    const canvas = this.canvasRef.current
    this.drawGarden(canvas)
  }

  render () {
    return (<canvas ref={this.canvasRef} width={(this.props.area.size_x + (this.outlineWidth * 2)) / 10} height={(this.props.area.size_y + (this.outlineWidth * 2)) / 10} />)
  }
}

GardenAreaDisplay.propTypes = {
  area: PropTypes.object.isRequired,
  gardenBeds: PropTypes.array.isRequired,
  squares: PropTypes.array.isRequired
}

class GardenDisplay extends React.Component {
  constructor (props) {
    super(props)

    this.state = {
      selectedArea: null,
      areas: [],
      beds: [],
      rows: [],
      squares: []
    }

    this.updateSelectedGardenArea = this.updateSelectedGardenArea.bind(this)
    this.updateGardenAreas = this.updateGardenAreas.bind(this)
    this.updateGardenBeds = this.updateGardenBeds.bind(this)
    this.updateGardenRows = this.updateGardenRows.bind(this)
    this.updateGardenSquares = this.updateGardenSquares.bind(this)
  }

  componentDidMount () {
    this.updateData()
    this.timer = setInterval(() => this.updateData(), 10000)
  }

  componentWillUnmount () {
    clearInterval(this.timer)
    this.timer = null
  }

  updateSelectedGardenArea (event) {
    const target = event.target
    const value = target.value

    if (value === 'none') {
      this.setState({ selectedArea: null })
    } else {
      this.setState({ selectedArea: Number(value) })
    }
  }

  updateGardenAreas (data) {
    this.setState({
      areas: data
    })
  }

  updateGardenBeds (data) {
    this.setState({
      beds: data
    })
  }

  updateGardenRows (data) {
    this.setState({
      rows: data
    })
  }

  updateGardenSquares (data) {
    this.setState({
      squares: data
    })
  }

  async updateData () {
    await $.getJSON('/garden/areas/', this.updateGardenAreas)
    await $.getJSON('/garden/beds/', this.updateGardenBeds)
    await $.getJSON('/garden/rows/', this.updateGardenRows)
    await $.getJSON('/garden/squares/', this.updateGardenSquares)
  }

  render () {
    const areas = [(<option key='blank' value='none'></option>)]
    for (const idx in this.state.areas) {
      const area = this.state.areas[idx]
      areas.push((<option key={area.pk} value={area.pk}>{area.name}</option>))
    }
    let areaView = null
    if (this.state.selectedArea !== null) {
      const area = this.state.areas.find((a) => a.pk === this.state.selectedArea)
      const beds = this.state.beds.filter((b) => b.area === area.pk)
      areaView = (<GardenAreaDisplay key={area.pk} area={area} gardenBeds={beds} squares={this.state.squares} />)
    }
    return (
      <>
        <select onChange={this.updateSelectedGardenArea}>{areas}</select>
        <div>
          {areaView}
        </div>
      </>
    )
  }
}

export { GardenDisplay }