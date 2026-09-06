import React from 'react'
import { Badge, Button } from 'react-bootstrap'

interface CatalogRecord {
  pk: number
  active: boolean
}

// Catalog collections carry retired records too, because the same list names
// the entry an old planting or receipt points at. A selector asks for the
// choices worth offering, keeping whatever the record being edited already
// holds so saving an unrelated correction cannot repoint it.
function activeChoices<Record extends CatalogRecord>(records: Array<Record>, keep?: number | null): Array<Record> {
  return records.filter((record) => record.active || (keep != null && record.pk === keep))
}

function RetiredBadge({ active }: { active: boolean }) {
  if (active) return null
  return (
    <Badge bg="secondary" className="ms-1">
      Retired
    </Badge>
  )
}

interface RetireButtonProps {
  active: boolean
  saving?: boolean
  onChange: (active: boolean) => void
}

function RetireButton({ active, saving = false, onChange }: RetireButtonProps) {
  return (
    <Button size="sm" variant={active ? 'outline-danger' : 'outline-success'} disabled={saving} onClick={() => onChange(!active)}>
      {active ? 'Retire' : 'Restore'}
    </Button>
  )
}

function retiredRowClass(active: boolean): string | undefined {
  return active ? undefined : 'text-body-secondary'
}

export { CatalogRecord, RetireButton, RetiredBadge, activeChoices, retiredRowClass }
