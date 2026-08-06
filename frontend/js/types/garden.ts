type GardenLengthUnit = 'mm' | 'cm' | 'm' | 'in' | 'ft'

interface GardenArea {
  pk: number
  name: string
  size_x: number
  size_y: number
  // Null until an operator states what one grid step measures. Nothing derives
  // an area from the raw integers before that.
  geometry_confirmed: boolean
  length_unit: GardenLengthUnit | null
  cell_length: string | null
  square_metres: string | null
}

interface ConfirmGardenGeometry {
  length_unit: GardenLengthUnit
  cell_length: string
  notes?: string
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

export { ConfirmGardenGeometry, GardenArea, GardenBed, GardenLengthUnit, GardenRow, GardenSquare }
