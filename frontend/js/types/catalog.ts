interface CatalogReference {
  relation: string
  noun: string
  count: number
  examples: Array<string>
}

interface CatalogRecordLabel {
  pk: number
  label: string
}

interface CatalogMergePreview {
  source: CatalogRecordLabel
  target: CatalogRecordLabel
  references: Array<CatalogReference>
  blockers: Array<string>
}

interface CatalogMergeResult {
  moved: Array<CatalogReference>
}

// `stays` is the mirror of a merge's `references`: a replacement moves nothing,
// so what the preview lists is what keeps pointing at the record being
// superseded. `identity_locked` says whether posted stock has frozen the fields
// in `identity_fields`, which is what decides between correcting the record and
// replacing it.
interface CatalogReplacementPreview {
  source: CatalogRecordLabel
  identity_locked: boolean
  identity_fields: Array<string>
  stays: Array<CatalogReference>
  blockers: Array<string>
}

interface CatalogReplacementResult<Record> {
  source: Record
  replacement: Record
  stayed: Array<CatalogReference>
}

export { CatalogMergePreview, CatalogMergeResult, CatalogReference, CatalogRecordLabel, CatalogReplacementPreview, CatalogReplacementResult }
