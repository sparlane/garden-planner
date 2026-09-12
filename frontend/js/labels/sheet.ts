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

export { SheetLayout, sheetLayout }
