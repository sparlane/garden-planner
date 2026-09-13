import React from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Alert, Button, Form } from 'react-bootstrap'
import { Link } from 'react-router'

import { numberPotFillPlant, PotFillContents } from '../api/container_fills'
import { queryKeys } from '../query'
import { errorsByField } from '../utils'

export function PotFillNumber({ pk, contents }: { pk: number; contents: PotFillContents }) {
  const cache = useQueryClient()
  const [plant, setPlant] = React.useState('')
  const candidates = contents.plants.filter((id) => !contents.numbered_plants.includes(id))
  const mutation = useMutation({
    mutationFn: (selected: number) => numberPotFillPlant(pk, selected),
    onSuccess: async () => {
      setPlant('')
      await Promise.all(
        [queryKeys.containerFills.all, queryKeys.inventory.all, queryKeys.plantings.all, queryKeys.locations.all, queryKeys.reports.all, queryKeys.labels.all].map((queryKey) =>
          cache.invalidateQueries({ queryKey })
        )
      )
    }
  })
  return (
    <Form
      className="my-3"
      onSubmit={(event) => {
        event.preventDefault()
        if (candidates.includes(Number(plant))) mutation.mutate(Number(plant))
      }}
    >
      <h3 className="h6">Number an occupied pot</h3>
      <p>Give one plant’s pot its own label identity. The plant stays in this fill with the same media share. Numbering is permanent.</p>
      <fieldset disabled={mutation.isPending}>
        <Form.Group controlId={`fill-${pk}-number-plant`} className="mb-2">
          <Form.Label>Plant whose pot needs a number</Form.Label>
          <Form.Select value={plant} onChange={(event) => setPlant(event.target.value)}>
            <option value="">Choose a plant</option>
            {candidates.map((id) => (
              <option key={id} value={id}>
                Plant #{id}
              </option>
            ))}
          </Form.Select>
        </Form.Group>
        {candidates.length === 0 && <p>No occupied anonymous pots remain in this fill.</p>}
        <Button type="submit" disabled={mutation.isPending || !candidates.includes(Number(plant))}>
          {mutation.isPending ? 'Numbering…' : 'Number this plant’s pot'}
        </Button>
      </fieldset>
      {mutation.isError && (
        <Alert className="mt-2" variant="danger">
          {Object.values(errorsByField(mutation.error)).join(' ') || mutation.error.message}
        </Alert>
      )}
      {mutation.isSuccess && (
        <Alert className="mt-2" variant="success">
          Pot numbered: <Link to={`/inventory/serialized-units/${mutation.data.container_unit}`}>{mutation.data.container_unit_code}</Link>.
        </Alert>
      )}
    </Form>
  )
}
