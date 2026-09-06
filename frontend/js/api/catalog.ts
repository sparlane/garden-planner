import { CatalogMergePreview, CatalogMergeResult } from '../types/catalog'
import { csrfPost, fetchAsJson } from '../utils'

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

export { mergeCatalogRecords, previewCatalogMerge }
