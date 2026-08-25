interface Supplier {
  pk: number
  name: string
  address: string
  gst_status: 'registered' | 'unregistered' | 'unknown'
  gst_number: string
  website: string
  notes: string
  is_system_default: boolean
}

interface SupplierCreate {
  name: string
  address?: string
  gst_status?: 'registered' | 'unregistered' | 'unknown'
  gst_number?: string
  website?: string
  notes?: string
}

export { Supplier, SupplierCreate }
