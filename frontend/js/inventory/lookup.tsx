import React, { useState } from 'react'
import { useMutation, useQuery } from '@tanstack/react-query'
import { Alert, Button, Form } from 'react-bootstrap'
import { useNavigate, useParams } from 'react-router'

import { queryKeys } from '../query'
import { labelTargetLabel, labelTargetRoute } from '../labels/target_types'
import { findPotByCode, findPotByNumber } from './code_lookup'
import { NumberedUnitDetails } from './unit_details'

function NumberedUnitLookup() {
  const [value, setValue] = useState('')
  const navigate = useNavigate()
  const lookup = useMutation({
    mutationFn: async (code: string) => {
      const found = await findPotByCode(code)
      if (found.unit) return { route: `/inventory/serialized-units/${found.unit.pk}` }
      if (found.target) {
        const route = labelTargetRoute(found.target.target_type, found.target.object_id)
        return { route, message: route ? '' : `${labelTargetLabel(found.target.target_type)} has no detail screen yet.` }
      }
      return { message: found.message }
    },
    onSuccess: (found) => {
      if (found.route) navigate(found.route)
    }
  })
  return (
    <div className="container py-3">
      <h1>Find a numbered pot</h1>
      <Form
        onSubmit={(event) => {
          event.preventDefault()
          if (value.trim()) lookup.mutate(value.trim())
        }}
      >
        <Form.Group controlId="inventory-lookup-code">
          <Form.Label>Pot number or code</Form.Label>
          <div className="d-flex gap-2">
            <Form.Control autoFocus value={value} placeholder="Scan a code, or type a number" onChange={(event) => setValue(event.target.value)} />
            <Button type="submit" disabled={lookup.isPending || !value.trim()}>
              {lookup.isPending ? 'Finding…' : 'Find'}
            </Button>
          </div>
        </Form.Group>
      </Form>
      {lookup.data?.message && (
        <Alert variant="warning" className="mt-3" role="status">
          {lookup.data.message}
        </Alert>
      )}
    </div>
  )
}

// Permanent printed-link contract: /#/inventory/serialized-units/by-number/N.
// Resolve the printed number before rendering; never assume it is a detail id.
function NumberedUnitByNumberRoute() {
  const { number = '' } = useParams()
  const lookup = useQuery({
    queryKey: queryKeys.inventory.serializedUnitByNumber(number),
    queryFn: async ({ signal }) => (/^\d+$/.test(number) ? await findPotByNumber(Number(number), signal) : undefined) ?? null
  })
  if (lookup.isPending) return <div className="container py-3">Finding pot…</div>
  if (lookup.isError) return <div className="container py-3">Could not look up this pot. Please try again.</div>
  if (!lookup.data) return <div className="container py-3">No pot has been issued that number.</div>
  return <NumberedUnitDetails key={lookup.data.pk} unitPk={lookup.data.pk} />
}

export { NumberedUnitLookup, NumberedUnitByNumberRoute }
