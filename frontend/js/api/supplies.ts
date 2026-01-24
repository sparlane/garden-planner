import { Supplier, SupplierCreate } from '../types/suppliers'
import { csrfPost, fetchAsJson } from '../utils'

function getSuppliers(): Promise<Array<Supplier>> {
  return fetchAsJson<Array<Supplier>>('/supplies/supplier/')
}

function addSupplier(supplier: SupplierCreate) {
  return csrfPost('/supplies/supplier/', supplier)
}

export { getSuppliers, addSupplier }
