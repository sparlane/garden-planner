import React from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Alert, Button, Col, Form, Row } from 'react-bootstrap'

import { confirmGardenGeometry } from '../api/garden'
import { queryKeys } from '../query'
import { GardenArea, GardenLengthUnit } from '../types/garden'

const LENGTH_UNIT_LABELS: Record<GardenLengthUnit, string> = {
  mm: 'Millimetres',
  cm: 'Centimetres',
  m: 'Metres',
  in: 'Inches',
  ft: 'Feet'
}

// Garden placements and sizes are bare integers. Until somebody says what one
// grid step measures, nothing can turn a square into an area, so this is the
// gate in front of every area-based calculation rather than a nicety.
function ConfirmGeometryForm({ area }: { area: GardenArea }) {
  const queryClient = useQueryClient()
  const [unit, setUnit] = React.useState<GardenLengthUnit>(area.length_unit ?? 'mm')
  const [cellLength, setCellLength] = React.useState(area.cell_length ?? '1')
  const [error, setError] = React.useState<string>()
  const mutation = useMutation({
    mutationFn: () => confirmGardenGeometry(area.pk, { length_unit: unit, cell_length: cellLength }),
    onSuccess: () => {
      setError(undefined)
      return queryClient.invalidateQueries({ queryKey: queryKeys.garden.all })
    },
    onError: (caught: unknown) => setError(caught instanceof Error ? caught.message : String(caught))
  })

  return (
    <div>
      {area.geometry_confirmed ? (
        <p className="mb-2">
          One grid step is {area.cell_length} {area.length_unit}, so this area measures {area.square_metres} m².
        </p>
      ) : (
        <Alert variant="warning" className="py-2">
          This area&apos;s measurements have no recorded unit, so nothing can work out its size. Confirm what one grid step is.
        </Alert>
      )}
      <Row className="g-2 align-items-end">
        <Col md={4}>
          <Form.Group controlId={`geometry-cell-length-${area.pk}`}>
            <Form.Label>One grid step is</Form.Label>
            <Form.Control value={cellLength} inputMode="decimal" onChange={(event) => setCellLength(event.target.value)} />
          </Form.Group>
        </Col>
        <Col md={4}>
          <Form.Group controlId={`geometry-length-unit-${area.pk}`}>
            <Form.Label>Unit</Form.Label>
            <Form.Select value={unit} onChange={(event) => setUnit(event.target.value as GardenLengthUnit)}>
              {Object.entries(LENGTH_UNIT_LABELS).map(([code, label]) => (
                <option key={code} value={code}>
                  {label}
                </option>
              ))}
            </Form.Select>
          </Form.Group>
        </Col>
        <Col md={4}>
          <Button disabled={cellLength.trim() === '' || mutation.isPending} onClick={() => mutation.mutate()}>
            {mutation.isPending ? 'Saving…' : area.geometry_confirmed ? 'Correct this' : 'Confirm'}
          </Button>
        </Col>
      </Row>
      {area.geometry_confirmed && <Form.Text className="text-muted">Correcting this leaves earlier applications exactly as they were recorded.</Form.Text>}
      {error && (
        <Alert variant="danger" className="mt-2 mb-0 py-1 px-2">
          {error}
        </Alert>
      )}
    </div>
  )
}

export { ConfirmGeometryForm, LENGTH_UNIT_LABELS }
