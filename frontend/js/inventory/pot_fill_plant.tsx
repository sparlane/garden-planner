import React from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Alert, Button, Form } from 'react-bootstrap'
import { Link } from 'react-router'

import { plantPotFill } from '../api/container_fills'
import { getNurseryRegister } from '../api/plantings'
import { EMPTY_SELECTION, RegisterSelection, RegisterTable } from '../plantings/register_list'
import { queryKeys } from '../query'
import { NurseryRegisterFilters } from '../types/plantings'
import { errorsByField } from '../utils'

export function PotFillPlant({ pk, currentPlants }: { pk: number; currentPlants: number[] }) {
  const cache = useQueryClient()
  const [expanded, setExpanded] = React.useState(false)
  const [search, setSearch] = React.useState('')
  const [page, setPage] = React.useState(1)
  const [selection, setSelection] = React.useState<RegisterSelection>(EMPTY_SELECTION)
  const [overrideReason, setOverrideReason] = React.useState('')
  const filters: NurseryRegisterFilters = { search, page, page_size: 25, state: ['growing', 'available', 'retained'], quarantined: false, reserved: false }
  const plants = useQuery({
    queryKey: queryKeys.plantings.register(filters),
    queryFn: ({ signal }) => getNurseryRegister(filters, signal),
    enabled: expanded
  })
  const ids = selection.mode === 'ids' ? selection.ids : []
  const mutation = useMutation({
    mutationFn: (selected: number[]) => plantPotFill(pk, { plants: selected, override_reason: overrideReason }),
    onSuccess: async () => {
      setSelection(EMPTY_SELECTION)
      setOverrideReason('')
      await Promise.all(
        [
          queryKeys.containerFills.all,
          queryKeys.plantings.all,
          queryKeys.inventory.all,
          queryKeys.locations.all,
          queryKeys.costing.all,
          queryKeys.reports.all,
          queryKeys.seedTrays.all
        ].map((queryKey) => cache.invalidateQueries({ queryKey }))
      )
    }
  })
  return (
    <div className="my-3">
      <Button variant="outline-primary" onClick={() => setExpanded(!expanded)} disabled={mutation.isPending} aria-expanded={expanded}>
        {expanded ? 'Hide planting controls' : 'Plant into this fill'}
      </Button>
      {expanded && (
        <Form
          className="mt-3"
          onSubmit={(event) => {
            event.preventDefault()
            if (ids.length > 0) mutation.mutate([...ids])
          }}
        >
          <p>Move selected plants into unused pots in this fill, one plant per pot. All plants move together; if any cannot move, none move.</p>
          <p>
            Plants leave their current containers now. Available pots and destination capacity are checked when you confirm. Pots already used by departed plants cannot be assigned
            again.
          </p>
          <fieldset disabled={mutation.isPending}>
            <Form.Group controlId={`fill-${pk}-plant-search`} className="mb-2">
              <Form.Label>Search plants</Form.Label>
              <Form.Control
                value={search}
                onChange={(event) => {
                  setSearch(event.target.value)
                  setPage(1)
                }}
              />
            </Form.Group>
            {plants.isPending && <p>Loading plants…</p>}
            {plants.isError && <Alert variant="danger">Could not load plants.</Alert>}
            {plants.data && (
              <>
                <RegisterTable rows={plants.data.results.filter((plant) => !currentPlants.includes(plant.pk))} selection={selection} setSelection={setSelection} />
                <div className="d-flex align-items-center gap-2 mb-2">
                  <Button variant="outline-secondary" size="sm" disabled={!plants.data.previous} onClick={() => setPage(page - 1)}>
                    Previous
                  </Button>
                  <span>Page {page}</span>
                  <Button variant="outline-secondary" size="sm" disabled={!plants.data.next} onClick={() => setPage(page + 1)}>
                    Next
                  </Button>
                </div>
              </>
            )}
            <p>{ids.length} plants selected across all pages and searches.</p>
            {ids.map((plant) => (
              <span key={plant} className="d-inline-block me-3 mb-2">
                <Link to={`/plantings/plants/${plant}`}>Plant #{plant}</Link>{' '}
                <Button
                  size="sm"
                  variant="outline-secondary"
                  aria-label={`Remove plant ${plant}`}
                  onClick={() => setSelection({ mode: 'ids', ids: ids.filter((id) => id !== plant) })}
                >
                  Remove
                </Button>
              </span>
            ))}
            <Form.Group controlId={`fill-${pk}-capacity-override`} className="mb-2">
              <Form.Label>Capacity override reason (optional)</Form.Label>
              <Form.Control value={overrideReason} onChange={(event) => setOverrideReason(event.target.value)} />
              <Form.Text>Leave blank to enforce location capacity.</Form.Text>
            </Form.Group>
            <Button type="submit" disabled={ids.length === 0 || mutation.isPending}>
              {mutation.isPending ? 'Planting…' : `Confirm planting ${ids.length} plants`}
            </Button>
          </fieldset>
          {mutation.isError && (
            <Alert className="mt-2" variant="danger">
              {Object.values(errorsByField(mutation.error)).join(' ') || mutation.error.message}
            </Alert>
          )}
          {mutation.isSuccess && (
            <Alert className="mt-2" variant="success">
              Planted {mutation.variables.length} plants into this fill.
            </Alert>
          )}
        </Form>
      )}
    </div>
  )
}
