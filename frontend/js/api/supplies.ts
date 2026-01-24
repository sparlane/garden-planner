import { Supplier } from '../types/suppliers'
import { fetchAsJson } from '../utils'

function getSuppliers(): Promise<Array<Supplier>> {
  return fetchAsJson<Array<Supplier>>('/supplies/supplier/')
}

export { getSuppliers }
