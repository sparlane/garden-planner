interface CatalogMergeReference {
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
  references: Array<CatalogMergeReference>
  blockers: Array<string>
}

interface CatalogMergeResult {
  moved: Array<CatalogMergeReference>
}

export { CatalogMergePreview, CatalogMergeReference, CatalogMergeResult, CatalogRecordLabel }
