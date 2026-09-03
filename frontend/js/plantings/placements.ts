import { PlantPlacementType } from '../types/plantings'

// The four kinds of place a plant can be, in a grower's words rather than the
// column names. The plant's own history, the batch screen and the bulk review
// all name a placement, and a screen writing `container_unit` into its own
// wording would describe the same pot differently on each of them.
const PLACEMENT_LABELS: Record<PlantPlacementType, string> = {
  seed_tray_cell: 'Seed tray cell',
  garden_square: 'Garden square',
  location: 'Nursery location',
  container_unit: 'Numbered container'
}

// Only the fields that say where a placement is, so a location row, a batch
// row and a move payload can all be named without agreeing on anything else.
interface PlacementLike {
  location_type: PlantPlacementType
  seed_tray_cell?: number | null
  garden_square?: number | null
  location?: number | null
  container_unit?: number | null
  container_unit_code?: string | null
}

// A pot is named by the code printed on it, because that is what somebody
// standing in front of it can read; every other kind of place is drawn on a
// screen and is identified by the row it came from. The identifier is dropped
// rather than rendered as `#undefined` when a caller carries only the type.
function placementLabel(placement: PlacementLike): string {
  const label = PLACEMENT_LABELS[placement.location_type]
  if (placement.location_type === 'container_unit') {
    if (placement.container_unit_code) {
      return `${label} ${placement.container_unit_code}`
    }
    return placement.container_unit ? `${label} #${placement.container_unit}` : label
  }
  const identifier =
    placement.location_type === 'seed_tray_cell' ? placement.seed_tray_cell : placement.location_type === 'garden_square' ? placement.garden_square : placement.location
  return identifier ? `${label} #${identifier}` : label
}

export { PLACEMENT_LABELS, placementLabel }
