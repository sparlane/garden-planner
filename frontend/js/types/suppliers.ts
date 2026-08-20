interface Supplier {
  pk: number
  name: string
  website: string
  notes: string
  is_system_default: boolean
}

interface SupplierCreate {
  name: string
  website?: string
  notes?: string
}

export { Supplier, SupplierCreate }
