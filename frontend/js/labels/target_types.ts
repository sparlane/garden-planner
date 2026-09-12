// What kind of record a label is stuck to, and what that kind is called on
// screen. The key is the content type's model name, which is both what
// `/labels/identities/` filters on and what it sends back on every row, so the
// filter control and the table column cannot end up describing one record two
// different ways. The order is the order the picker offers them in: the
// containers a grower prints in bulk first, the places that are labelled once
// and never again last.
const LABEL_TARGET_TYPES = [
  ['inventoryunit', 'Numbered container'],
  ['specificplant', 'Plant'],
  ['plantcohort', 'Cohort'],
  ['seedtray', 'Seed tray'],
  ['productionbatch', 'Batch'],
  ['location', 'Location'],
  ['gardenarea', 'Garden area']
] as const

const LABEL_TARGET_LABELS: Record<string, string> = Object.fromEntries(LABEL_TARGET_TYPES)

// A target type the server has learned to label and this list has not yet
// caught up with still names itself, rather than leaving a blank cell that
// reads as a record with no kind at all.
function labelTargetLabel(targetType: string): string {
  return LABEL_TARGET_LABELS[targetType] ?? targetType
}

export { LABEL_TARGET_LABELS, LABEL_TARGET_TYPES, labelTargetLabel }
