import { CostLayer, CostSourceType } from '../types/costing'

// What each kind of input actually was, in a grower's words rather than the
// subledger's column names. The batch breakdown and the plant breakdown both
// list layers, and a screen inventing its own wording would describe the same
// input two ways depending on which one you were looking at.
const COST_SOURCE_LABELS: Record<CostSourceType, string> = {
  sowing_posting: 'Seed drawn by a sowing',
  application_line: 'Input applied',
  generation_residual: 'Discarded when the tray was cleaned',
  garden_planting: 'Bought as a plant',
  container_unit: 'Container sold with the plant'
}

// A container names the pot rather than the lot, because that is the thing the
// grower can point at: the lot behind it holds the rest of the box.
function costSourceLabel(layer: CostLayer): string {
  if (layer.source_type === 'container_unit' && layer.container_unit !== null) {
    return `${COST_SOURCE_LABELS.container_unit} #${layer.container_unit}`
  }
  return COST_SOURCE_LABELS[layer.source_type]
}

export { COST_SOURCE_LABELS, costSourceLabel }
