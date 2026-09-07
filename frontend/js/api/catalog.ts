import { CatalogMergePreview, CatalogMergeResult, CatalogReplacementPreview, CatalogReplacementResult } from '../types/catalog'
import { csrfPatch, csrfPost, fetchAsJson } from '../utils'

// Families, plants, varieties and suppliers all merge through the one action
// the server puts on every mergeable collection, so these take the collection
// path rather than being written out four times per verb.
function previewCatalogMerge(collection: string, pk: number, into: number, signal?: AbortSignal): Promise<CatalogMergePreview> {
  return fetchAsJson<CatalogMergePreview>(`${collection}${pk}/merge/?into=${into}`, signal)
}

async function mergeCatalogRecords(collection: string, pk: number, into: number): Promise<CatalogMergeResult> {
  const response = await csrfPost(`${collection}${pk}/merge/`, { into })
  return response.json() as Promise<CatalogMergeResult>
}

function previewCatalogReplacement(collection: string, pk: number, signal?: AbortSignal): Promise<CatalogReplacementPreview> {
  return fetchAsJson<CatalogReplacementPreview>(`${collection}${pk}/replace/`, signal)
}

// Both corrections send only the fields that should read differently: the
// server carries the rest of the record over, so a screen cannot drop the half
// of it that has no control on the form.
async function replaceCatalogRecord<Record>(collection: string, pk: number, changes: object): Promise<CatalogReplacementResult<Record>> {
  const response = await csrfPost(`${collection}${pk}/replace/`, changes)
  return response.json() as Promise<CatalogReplacementResult<Record>>
}

async function correctCatalogRecord<Record>(collection: string, pk: number, changes: object): Promise<Record> {
  const response = await csrfPatch(`${collection}${pk}/`, changes)
  return response.json() as Promise<Record>
}

export { correctCatalogRecord, mergeCatalogRecords, previewCatalogMerge, previewCatalogReplacement, replaceCatalogRecord }
