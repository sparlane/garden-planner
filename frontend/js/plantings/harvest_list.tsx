import React from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Alert, Button, Form, Table } from 'react-bootstrap'

import { AttachmentGallery, AttachmentUploader } from '../attachments'
import { reverseHarvest } from '../api/plantings'
import { queryKeys } from '../query'
import { formatDateTime, formatMeasure } from '../utils'
import { HARVEST_UNIT_LABELS, Harvest, HarvestFamilyTotal, HarvestGrade } from '../types/plantings'
import { invalidateHarvests } from './harvest_form'

const GRADE_LABELS: Record<HarvestGrade, string> = {
  ungraded: 'Ungraded',
  premium: 'Premium',
  standard: 'Standard',
  seconds: 'Seconds'
}

// A total and the unit it was measured in belong together, so they are never
// rendered apart. Families sit in their own cells because count, mass, and
// volume are not addable.
function FamilyTotals({ totals, empty = '—' }: { totals: Array<HarvestFamilyTotal>; empty?: string }) {
  if (totals.length === 0) {
    return <>{empty}</>
  }
  return (
    <>
      {totals.map((total, index) => (
        <span key={total.conversion_family}>
          {index > 0 && <span className="text-muted"> · </span>}
          {formatMeasure(total.quantity, HARVEST_UNIT_LABELS[total.unit_code])}
        </span>
      ))}
    </>
  )
}

function ReverseHarvestButton({ harvest }: { harvest: Harvest }) {
  const queryClient = useQueryClient()
  const [reason, setReason] = React.useState('')
  const [open, setOpen] = React.useState(false)
  const [error, setError] = React.useState<string>()
  const mutation = useMutation({
    mutationFn: () => reverseHarvest(harvest.pk, { reason }),
    onSuccess: () => {
      setOpen(false)
      setReason('')
      setError(undefined)
      return invalidateHarvests(queryClient, harvest.batch, false)
    },
    onError: (caught: unknown) => setError(caught instanceof Error ? caught.message : String(caught))
  })

  if (harvest.status === 'reversed') {
    return <span className="text-muted">Reversed</span>
  }
  if (!open) {
    return (
      <Button size="sm" variant="outline-danger" onClick={() => setOpen(true)}>
        Reverse
      </Button>
    )
  }
  return (
    <div className="d-flex flex-column gap-1">
      <Form.Control size="sm" value={reason} placeholder="Why was this wrong?" onChange={(event) => setReason(event.target.value)} />
      <div className="d-flex gap-1">
        <Button size="sm" variant="danger" disabled={!reason.trim() || mutation.isPending} onClick={() => mutation.mutate()}>
          {mutation.isPending ? 'Reversing…' : 'Confirm'}
        </Button>
        <Button size="sm" variant="outline-secondary" onClick={() => setOpen(false)}>
          Cancel
        </Button>
      </div>
      {error && (
        <Alert className="mb-0 py-1 px-2" variant="danger">
          {error}
        </Alert>
      )}
    </div>
  )
}

interface HarvestTableProps {
  harvests: Array<Harvest>
  showCrop?: boolean
  showLocation?: boolean
}

function HarvestTable({ harvests, showCrop = true, showLocation = true }: HarvestTableProps) {
  const queryClient = useQueryClient()
  if (harvests.length === 0) {
    return <p className="text-muted mb-0">No harvests recorded yet.</p>
  }
  return (
    <Table size="sm" responsive>
      <thead>
        <tr>
          <th>Harvested</th>
          {showCrop && <th>Crop</th>}
          <th>Quantity</th>
          {showLocation && <th>Location</th>}
          <th>Grade</th>
          <th>Quality</th>
          <th>Plants</th>
          <th>Notes</th>
          <th />
        </tr>
      </thead>
      <tbody>
        {harvests.map((harvest) => {
          const reversed = harvest.status === 'reversed'
          return (
            // Only the quantity is struck through, because the quantity is what
            // stopped counting. Striking the whole row would also strike the
            // reason it was retracted, which no descendant style can undo.
            <tr key={harvest.pk} className={reversed ? 'text-muted' : undefined}>
              <td>{formatDateTime(harvest.harvested_at)}</td>
              {showCrop && (
                <td>
                  {harvest.batch_code} · {harvest.plant_name} — {harvest.variety_name}
                </td>
              )}
              <td className={reversed ? 'text-decoration-line-through' : undefined}>{formatMeasure(harvest.quantity, HARVEST_UNIT_LABELS[harvest.unit_code])}</td>
              {showLocation && <td>{harvest.location_label ?? '—'}</td>}
              <td>{GRADE_LABELS[harvest.grade]}</td>
              <td>{harvest.quality_rating ?? '—'}</td>
              <td>
                {harvest.plants.length === 0 ? '—' : harvest.plants.length}
                {harvest.finished_plants.length > 0 && <span className="ms-1 text-muted">({harvest.finished_plants.length} finished)</span>}
              </td>
              <td>
                {harvest.notes || '—'}
                {reversed && harvest.reverse_reason && <div>Reversed: {harvest.reverse_reason}</div>}
                <AttachmentGallery attachments={harvest.attachments} />
                <details className="mt-2">
                  <summary>Add photos</summary>
                  <AttachmentUploader
                    id={`harvest-photos-${harvest.pk}`}
                    targetType="harvest"
                    targetId={harvest.pk}
                    onUploaded={() => void queryClient.invalidateQueries({ queryKey: queryKeys.plantings.harvestsAll })}
                  />
                </details>
              </td>
              <td className="text-nowrap">
                <ReverseHarvestButton harvest={harvest} />
              </td>
            </tr>
          )
        })}
      </tbody>
    </Table>
  )
}

export { FamilyTotals, GRADE_LABELS, HarvestTable, ReverseHarvestButton }
