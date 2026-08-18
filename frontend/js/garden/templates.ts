import { GardenBedKind, GardenChildCreate } from '../types/garden'

// The layouts a household garden is actually made of. A template is a shape
// plus what the gardener called it; the geometry it expands into is ordinary
// beds, rows, and squares, so nothing here creates a private idea of a place.
type LayoutTemplate = 'in_ground_bed' | 'raised_bed' | 'rows' | 'square_foot_grid'

interface LayoutTemplateInfo {
  value: LayoutTemplate
  label: string
  description: string
  kind: GardenBedKind
  // Whether the gardener is asked how many strips or cells to divide the bed
  // into. A plain bed is undivided until they say otherwise.
  divides: 'none' | 'rows' | 'grid'
}

const LAYOUT_TEMPLATES: Array<LayoutTemplateInfo> = [
  {
    value: 'in_ground_bed',
    label: 'In-ground bed',
    description: 'A patch of open ground, undivided.',
    kind: 'in_ground',
    divides: 'none'
  },
  {
    value: 'raised_bed',
    label: 'Raised bed',
    description: 'A built bed, undivided.',
    kind: 'raised',
    divides: 'none'
  },
  {
    value: 'rows',
    label: 'Rows',
    description: 'A bed divided into strips running its width.',
    kind: 'in_ground',
    divides: 'rows'
  },
  {
    value: 'square_foot_grid',
    label: 'Square-foot grid',
    description: 'A bed marked out in equal cells.',
    kind: 'raised',
    divides: 'grid'
  }
]

interface LayoutPlan {
  template: LayoutTemplate
  name: string
  placement_x: number
  placement_y: number
  size_x: number
  size_y: number
  // How many strips a rows template lays, or how many cells across and up a
  // grid template marks out. Ignored by the undivided templates.
  divisions: number
  columns: number
}

interface ExpandedLayout {
  kind: GardenBedKind
  rows: Array<Omit<GardenChildCreate, 'bed'>>
  squares: Array<Omit<GardenChildCreate, 'bed'>>
}

function templateInfo(template: LayoutTemplate): LayoutTemplateInfo {
  // The list is the only source of templates, so a value outside it cannot be
  // chosen; falling back to the first keeps the type honest without inventing
  // a shape nobody asked for.
  return LAYOUT_TEMPLATES.find((candidate) => candidate.value === template) ?? LAYOUT_TEMPLATES[0]
}

// Strips run the width of the bed and are spaced evenly up it, leaving the
// remainder as the path between them rather than silently widening the last
// strip. A bed too short to hold the strips asked for produces none, and the
// caller reports that rather than laying a zero-height row the server would
// refuse.
function expandRows(plan: LayoutPlan): Array<Omit<GardenChildCreate, 'bed'>> {
  const divisions = Math.max(1, Math.floor(plan.divisions))
  const pitch = Math.floor(plan.size_y / divisions)
  if (pitch < 1) {
    return []
  }

  const height = Math.max(1, Math.floor(pitch / 2))
  return Array.from({ length: divisions }, (_unused, index) => ({
    name: `Row ${index + 1}`,
    placement_x: 0,
    placement_y: index * pitch,
    size_x: plan.size_x,
    size_y: height
  }))
}

// Cells tile from the bed's origin and are all the same size, so a bed that is
// not an exact multiple keeps its remainder as unmarked ground at the far edge
// instead of being covered by a short final cell.
function expandGrid(plan: LayoutPlan): Array<Omit<GardenChildCreate, 'bed'>> {
  const columns = Math.max(1, Math.floor(plan.columns))
  const rowCount = Math.max(1, Math.floor(plan.divisions))
  const cellWidth = Math.floor(plan.size_x / columns)
  const cellHeight = Math.floor(plan.size_y / rowCount)
  if (cellWidth < 1 || cellHeight < 1) {
    return []
  }

  const squares: Array<Omit<GardenChildCreate, 'bed'>> = []
  for (let row = 0; row < rowCount; row += 1) {
    for (let column = 0; column < columns; column += 1) {
      squares.push({
        name: `${String.fromCharCode(65 + (row % 26))}${column + 1}`,
        placement_x: column * cellWidth,
        placement_y: row * cellHeight,
        size_x: cellWidth,
        size_y: cellHeight
      })
    }
  }
  return squares
}

// Turns one chosen template into the geometry it stands for. Placements are
// relative to the bed, which is where the server measures them from too.
function expandTemplate(plan: LayoutPlan): ExpandedLayout {
  const info = templateInfo(plan.template)
  return {
    kind: info.kind,
    rows: info.divides === 'rows' ? expandRows(plan) : [],
    squares: info.divides === 'grid' ? expandGrid(plan) : []
  }
}

export { ExpandedLayout, LAYOUT_TEMPLATES, LayoutPlan, LayoutTemplate, LayoutTemplateInfo, expandTemplate, templateInfo }
