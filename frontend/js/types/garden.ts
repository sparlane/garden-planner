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

// What the gardener said they were making. It changes nothing about how the
// rectangle is measured; it only lets a screen name what it is drawing.
type GardenBedKind = 'in_ground' | 'raised' | 'container'

interface GardenBed {
  pk: number
  area: number
  name: string
  kind: GardenBedKind
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

interface GardenAreaCreate {
  name: string
  size_x: number
  size_y: number
}

interface GardenBedCreate {
  area: number
  name: string
  kind: GardenBedKind
  placement_x: number
  placement_y: number
  size_x: number
  size_y: number
}

// A row and a square are placed the same way; only the bed they divide and the
// shape they take differ, so one create shape serves both.
interface GardenChildCreate {
  bed: number
  name: string
  placement_x: number
  placement_y: number
  size_x: number
  size_y: number
}

type GardenRowCreate = GardenChildCreate

type GardenSquareCreate = GardenChildCreate

export {
  ConfirmGardenGeometry,
  GardenArea,
  GardenAreaCreate,
  GardenBed,
  GardenBedCreate,
  GardenBedKind,
  GardenChildCreate,
  GardenLengthUnit,
  GardenRow,
  GardenRowCreate,
  GardenSquare,
  GardenSquareCreate
}
