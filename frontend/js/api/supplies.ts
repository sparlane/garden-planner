import { catalogSearchQuery } from './catalog'
import { Supplier, SupplierCreate } from '../types/suppliers'
import { csrfPatch, csrfPost, fetchAsJson } from '../utils'

function getSuppliers(signal?: AbortSignal, search?: string): Promise<Array<Supplier>> {
  return fetchAsJson<Array<Supplier>>(`/supplies/supplier/${catalogSearchQuery(search)}`, signal)
}

function addSupplier(supplier: SupplierCreate) {
  return csrfPost('/supplies/supplier/', supplier)
}

async function updateSupplier(pk: number, supplier: Partial<SupplierCreate>): Promise<Supplier> {
  const response = await csrfPatch(`/supplies/supplier/${pk}/`, supplier)
  return response.json() as Promise<Supplier>
}

export { getSuppliers, addSupplier, updateSupplier }
