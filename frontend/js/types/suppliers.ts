interface Supplier {
  pk: number
  name: string
  website: string
  notes: string
}

interface SupplierCreate {
  name: string
  website?: string
  notes?: string
}

export { Supplier, SupplierCreate }
