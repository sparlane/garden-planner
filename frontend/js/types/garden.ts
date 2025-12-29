interface GardenArea {
  pk: number
  name: string
  size_x: number
  size_y: number
}

interface GardenBed {
  pk: number
  area: number
  name: string
  placement_x: number
  placement_y: number
  size_x: number
  size_y: number
}

interface GardenRow {
  pk: number
  bed: number
  name: string
  placement_x: number
  placement_y: number
  size_x: number
  size_y: number
}

interface GardenSquare {
  pk: number
  bed: number
  area: number
  name: string
  placement_x: number
  placement_y: number
  size_x: number
  size_y: number
}

export { GardenArea, GardenBed, GardenRow, GardenSquare }
