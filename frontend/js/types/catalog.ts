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

// What the catalog already holds that means the name being typed. `reason` is
// how it resembles it, `handoff` is where a superseded record said the catalog
// carries on — retyping that name recreates the duplicate somebody cleaned up.
interface CatalogDuplicate {
  pk: number
  label: string
  active: boolean
  reason: string
  handoff: { relation: 'merged_into' | 'replaced_by'; pk: number; label: string } | null
}

// `name` and `normalized` are what was compared to reach the candidates, so a
// collection with no name typed on it reports neither: a seed catalog entry is
// one supplier's variety and no more, and holding those already is the
// duplicate rather than the scope one is looked for in.
interface CatalogDuplicateCheck {
  name?: string
  normalized?: string
  candidates: Array<CatalogDuplicate>
}

interface CatalogReplacementResult<Record> {
  source: Record
  replacement: Record
  stayed: Array<CatalogReference>
}

export {
  CatalogDuplicate,
  CatalogDuplicateCheck,
  CatalogMergePreview,
  CatalogMergeResult,
  CatalogReference,
  CatalogRecordLabel,
  CatalogReplacementPreview,
  CatalogReplacementResult
}
