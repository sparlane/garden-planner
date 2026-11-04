import { LabelDimensions, LabelTemplate } from '../types/labels'

// The geometry of one printed page, worked out once for both the screen that
// designs a template and the renderer that prints it. A designer told "3
// across, 8 down" and a printed page that disagreed would be worse than no
// preview at all, so neither side is allowed its own arithmetic.
interface SheetLayout {
  columns: number
  rows: number
  perPage: number
  pageWidthMm: number
  pageHeightMm: number
  marginSideMm: number
  marginTopMm: number
  columnGapMm: number
  rowGapMm: number
}

// How many whole tracks of `size` fit in `available` once the gaps between
// them are paid for. The last track takes no trailing gap, which is why this
// is not a plain division: at 70mm with no gap three tracks need exactly the
// 210mm of an A4 sheet, and rounding that down to two would cost a third of
// every page.
function tracks(available: number, size: number, gap: number): number {
  if (size <= 0 || available < size) return 0
  return Math.max(1, Math.floor((available + gap) / (size + gap)))
}

// A sheet's side margin is the unprintable edge down both sides and its top
// margin the bar above the first row, which on LC24 stock is a different
// measurement; the same asymmetry runs through the gaps, which commonly part
// the columns of a sheet while its rows sit flush. A template stating only
// `margin_mm` and `gap_mm` is symmetrical and the other two follow them. A
// roll or a single label has no page to be placed on, so it is one label wide
// and one deep with nothing around it.
function sheetLayout(template: Pick<LabelTemplate, 'layout' | 'dimensions'>): SheetLayout {
  const dimensions: LabelDimensions = template.dimensions
  const sheet = template.layout === 'sheet'
  const pageWidthMm = dimensions.page_width_mm ?? dimensions.label_width_mm
  const pageHeightMm = dimensions.page_height_mm ?? dimensions.label_height_mm
  const marginSideMm = sheet ? (dimensions.margin_mm ?? 0) : 0
  const marginTopMm = sheet ? (dimensions.margin_top_mm ?? dimensions.margin_mm ?? 0) : 0
  const columnGapMm = sheet ? (dimensions.gap_mm ?? 0) : 0
  const rowGapMm = sheet ? (dimensions.row_gap_mm ?? dimensions.gap_mm ?? 0) : 0
  const columns = sheet ? tracks(pageWidthMm - 2 * marginSideMm, dimensions.label_width_mm, columnGapMm) : 1
  const rows = sheet ? tracks(pageHeightMm - 2 * marginTopMm, dimensions.label_height_mm, rowGapMm) : 1
  return { columns, rows, perPage: columns * rows, pageWidthMm, pageHeightMm, marginSideMm, marginTopMm, columnGapMm, rowGapMm }
}

// The space between a label's edge and its contents, in millimetres. Every
// label keeps `BASE_PADDING_MM`; one that sits within the printer's unprintable
// strip is padded by however much of its contents would otherwise fall in it.
// Only a sheet runs labels to the paper, and only the labels on the outer
// column or row are affected, so the same template pads its middle labels less
// than its edge ones. `index` is the label's place in the run, which wraps onto
// each new page.
const BASE_PADDING_MM = 2

interface LabelInsets {
  top: number
  right: number
  bottom: number
  left: number
}

function labelInsets(template: Pick<LabelTemplate, 'layout' | 'dimensions'>, index: number): LabelInsets {
  const base = { top: BASE_PADDING_MM, right: BASE_PADDING_MM, bottom: BASE_PADDING_MM, left: BASE_PADDING_MM }
  const unprintable = template.dimensions.unprintable_mm ?? 0
  if (template.layout !== 'sheet' || unprintable <= 0) return base
  const sheet = sheetLayout(template)
  if (sheet.perPage <= 0) return base
  const dimensions = template.dimensions
  const column = (index % sheet.perPage) % sheet.columns
  const row = Math.floor((index % sheet.perPage) / sheet.columns)
  const left = sheet.marginSideMm + column * (dimensions.label_width_mm + sheet.columnGapMm)
  const top = sheet.marginTopMm + row * (dimensions.label_height_mm + sheet.rowGapMm)
  const right = sheet.pageWidthMm - left - dimensions.label_width_mm
  const bottom = sheet.pageHeightMm - top - dimensions.label_height_mm
  const inset = (distance: number) => Math.max(BASE_PADDING_MM, unprintable - distance + BASE_PADDING_MM)
  return { top: inset(top), right: inset(right), bottom: inset(bottom), left: inset(left) }
}

export { LabelInsets, SheetLayout, labelInsets, sheetLayout }
