import React from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Alert, Button, Card, Col, Form, Row, Table } from 'react-bootstrap'

import { createStockReceipt, getItemUnitConversions, updateStockReceipt } from '../api/inventory'
import { ApiError, formatMeasure, formatQuantity } from '../utils'
import {
  InputTaxSource,
  InventoryItem,
  InventoryUnit,
  PurchaseTaxTreatment,
  QuantityCertainty,
  ReceiptDocumentType,
  StockReceipt,
  StockReceiptLine,
  StockReceiptLineWrite,
  UnitCode
} from '../types/inventory'
import { Location } from '../types/locations'
import { Supplier } from '../types/suppliers'
import { queryKeys } from '../query'
import { documentErrors, invalidateReceipts, lineFieldErrors, localErrorMessage } from './receipt_list'

// The unit and the item conversion are one choice for the operator but two
// mutually exclusive fields on the wire, so the option value carries which.
type UnitChoice = `unit:${string}` | `conversion:${number}` | ''

interface ReceiptLineDraft {
  key: string
  item: number | ''
  quantityCertainty: QuantityCertainty
  // Kept as the operator typed it. Parsing to a number here would reintroduce
  // the float artifacts the 9-decimal-place string contract exists to avoid.
  quantity: string
  unitChoice: UnitChoice
  supplierLotReference: string
  expiresOn: string
  supplierCostInclTax: string
  taxTreatment: PurchaseTaxTreatment
  taxRate: string
  inputTaxSource: InputTaxSource
  inputTaxAmount: string
  claimInputTax: boolean
  claimablePercentage: string
  apportionmentBasis: string
  destination: number | ''
  // The last quantity the server normalized. Null means nobody has checked
  // this line's arithmetic since it was last touched.
  baseQuantity: string | null
  baseUnit: UnitCode | null
}

function blankLine(key: string): ReceiptLineDraft {
  return {
    key,
    item: '',
    quantityCertainty: 'exact',
    quantity: '',
    unitChoice: '',
    supplierLotReference: '',
    expiresOn: '',
    supplierCostInclTax: '',
    taxTreatment: 'unknown',
    taxRate: '0',
    inputTaxSource: 'none',
    inputTaxAmount: '0',
    claimInputTax: false,
    claimablePercentage: '0',
    apportionmentBasis: '',
    destination: '',
    baseQuantity: null,
    baseUnit: null
  }
}

function hydrateLine(line: StockReceiptLine, key: string): ReceiptLineDraft {
  return {
    key,
    item: line.item,
    quantityCertainty: line.quantity_certainty,
    quantity: line.quantity === null ? '' : formatQuantity(line.quantity),
    unitChoice: line.unit_conversion !== null ? `conversion:${line.unit_conversion}` : line.unit_code !== null ? `unit:${line.unit_code}` : '',
    supplierLotReference: line.supplier_lot_reference,
    expiresOn: line.expires_on ?? '',
    supplierCostInclTax: line.supplier_cost_incl_tax,
    taxTreatment: line.tax_treatment,
    taxRate: line.tax_rate,
    inputTaxSource: line.input_tax_source,
    inputTaxAmount: line.input_tax_amount,
    claimInputTax: line.claim_input_tax,
    claimablePercentage: line.claimable_percentage,
    apportionmentBasis: line.apportionment_basis,
    destination: line.destination,
    baseQuantity: line.base_quantity,
    baseUnit: line.base_unit
  }
}

function linePayload(line: ReceiptLineDraft): StockReceiptLineWrite {
  const unknown = line.quantityCertainty === 'unknown'
  const [kind, value] = line.unitChoice.split(':')
  return {
    item: Number(line.item),
    supplier_lot_reference: line.supplierLotReference,
    expires_on: line.expiresOn === '' ? null : line.expiresOn,
    quantity: unknown ? null : line.quantity,
    quantity_certainty: line.quantityCertainty,
    // Both keys are always sent. The server requires exactly one to be set, and
    // omitting the unused one would leave a stale value behind on a PATCH.
    unit_code: kind === 'unit' ? (value as UnitCode) : null,
    unit_conversion: kind === 'conversion' ? Number(value) : null,
    supplier_cost_incl_tax: line.supplierCostInclTax,
    tax_treatment: line.taxTreatment,
    tax_rate: line.taxRate,
    input_tax_source: line.inputTaxSource,
    input_tax_amount: line.inputTaxAmount,
    claim_input_tax: line.claimInputTax,
    claimable_percentage: line.claimablePercentage,
    apportionment_basis: line.apportionmentBasis,
    destination: Number(line.destination)
  }
}

function receivableLocations(locations: Array<Location>) {
  // Seed packet containers are system-managed, and SYSTEM-TRAY-UNKNOWN exists
  // only so the tray migration had somewhere to put unplaced assets.
  return locations.filter((location) => location.location_type !== 'seed_packet' && location.code !== 'SYSTEM-TRAY-UNKNOWN')
}

function receivableItems(items: Array<InventoryItem>, seedTrayItemIds: Set<number>) {
  return items.filter((item) => item.category !== 'seed' && (item.tracking_mode !== 'serialized' || seedTrayItemIds.has(item.pk)))
}

interface LineRowProps {
  line: ReceiptLineDraft
  index: number
  items: Array<InventoryItem>
  locations: Array<Location>
  units: Array<InventoryUnit>
  errors: Record<string, string>
  removable: boolean
  onChange: (key: string, patch: Partial<ReceiptLineDraft>, renormalize?: boolean) => void
  onRemove: (key: string) => void
}

function LineRow({ line, index, items, locations, units, errors, removable, onChange, onRemove }: LineRowProps) {
  const chosenItem = items.find((item) => item.pk === line.item)
  const serializedTray = chosenItem?.tracking_mode === 'serialized'
  const { data: conversions = [] } = useQuery({
    queryKey: queryKeys.inventory.conversions(Number(line.item)),
    queryFn: ({ signal }) => getItemUnitConversions(Number(line.item), signal),
    enabled: line.item !== ''
  })

  // Compatibility is by conversion family, not dimension: seeds and loose
  // countable things are both counts but cannot be converted between.
  const family = units.find((unit) => unit.code === chosenItem?.base_unit)?.reference_unit
  const standardUnits = family === undefined ? [] : units.filter((unit) => unit.reference_unit === family)
  const packageUnits = conversions.filter((conversion) => conversion.active)
  const unknown = line.quantityCertainty === 'unknown'

  return (
    <tr>
      <td>
        <Form.Select
          size="sm"
          value={line.item}
          isInvalid={errors.item !== undefined}
          onChange={(event) => {
            const item = items.find((entry) => entry.pk === Number(event.target.value))
            onChange(
              line.key,
              {
                item: item?.pk ?? '',
                quantityCertainty: item?.tracking_mode === 'serialized' ? 'exact' : line.quantityCertainty,
                unitChoice: item?.tracking_mode === 'serialized' ? 'unit:each' : ''
              },
              true
            )
          }}
        >
          <option value="">Select an item</option>
          {items.map((item) => (
            <option key={item.pk} value={item.pk}>
              {item.name} ({item.base_unit})
            </option>
          ))}
        </Form.Select>
        {errors.item && <Form.Text className="text-danger">{errors.item}</Form.Text>}
      </td>
      <td>
        <Form.Select
          size="sm"
          value={line.quantityCertainty}
          disabled={serializedTray}
          isInvalid={errors.quantity_certainty !== undefined}
          onChange={(event) => onChange(line.key, { quantityCertainty: event.target.value as QuantityCertainty }, true)}
        >
          <option value="exact">Exact</option>
          <option value="estimated">Estimated</option>
          <option value="unknown">Unknown</option>
        </Form.Select>
        {errors.quantity_certainty && <Form.Text className="text-danger">{errors.quantity_certainty}</Form.Text>}
        <Form.Control
          size="sm"
          className="mt-1"
          value={unknown ? '' : line.quantity}
          disabled={unknown}
          inputMode="decimal"
          placeholder={unknown ? 'Not counted' : 'e.g. 2'}
          isInvalid={errors.quantity !== undefined}
          onChange={(event) => onChange(line.key, { quantity: event.target.value }, true)}
        />
        {errors.quantity && <Form.Text className="text-danger">{errors.quantity}</Form.Text>}
        {serializedTray && <Form.Text muted>Posting creates one numbered tray asset per whole each.</Form.Text>}
      </td>
      <td>
        <Form.Select
          size="sm"
          value={line.unitChoice}
          disabled={line.item === ''}
          isInvalid={errors.unit_code !== undefined || errors.unit_conversion !== undefined}
          onChange={(event) => onChange(line.key, { unitChoice: event.target.value as UnitChoice }, true)}
        >
          <option value="">Select a unit</option>
          {standardUnits.map((unit) => (
            <option key={unit.code} value={`unit:${unit.code}`}>
              {unit.label} ({unit.code})
            </option>
          ))}
          {packageUnits.map((conversion) => (
            <option key={conversion.pk} value={`conversion:${conversion.pk}`}>
              {conversion.label} (× {formatQuantity(conversion.multiplier)} {chosenItem?.base_unit})
            </option>
          ))}
        </Form.Select>
        {(errors.unit_code || errors.unit_conversion) && <Form.Text className="text-danger">{errors.unit_code ?? errors.unit_conversion}</Form.Text>}
      </td>
      <td className="small">
        {unknown ? (
          <span className="text-muted">Unknown — claims no balance</span>
        ) : line.baseQuantity !== null ? (
          formatMeasure(line.baseQuantity, line.baseUnit ?? '')
        ) : (
          <span className="text-muted">Save the draft to normalize</span>
        )}
      </td>
      <td>
        <Form.Select
          size="sm"
          value={line.destination}
          isInvalid={errors.destination !== undefined}
          onChange={(event) => onChange(line.key, { destination: event.target.value === '' ? '' : Number(event.target.value) })}
        >
          <option value="">Select a place</option>
          {locations.map((location) => (
            <option key={location.pk} value={location.pk}>
              {location.name}
            </option>
          ))}
        </Form.Select>
        {errors.destination && <Form.Text className="text-danger">{errors.destination}</Form.Text>}
      </td>
      <td>
        <Form.Label className="small mb-0">Supplier cost incl tax</Form.Label>
        <Form.Control
          size="sm"
          type="number"
          min="0"
          step="0.0001"
          value={line.supplierCostInclTax}
          isInvalid={errors.supplier_cost_incl_tax !== undefined}
          onChange={(event) => onChange(line.key, { supplierCostInclTax: event.target.value })}
        />
        <Form.Select
          size="sm"
          className="mt-1"
          value={line.taxTreatment}
          onChange={(event) => onChange(line.key, { taxTreatment: event.target.value as PurchaseTaxTreatment, taxRate: event.target.value === 'standard' ? line.taxRate : '0' })}
        >
          <option value="unknown">Tax treatment unknown</option>
          <option value="standard">Standard-rated</option>
          <option value="zero_rated">Zero-rated</option>
          <option value="exempt">Exempt</option>
          <option value="out_of_scope">Out of scope</option>
        </Form.Select>
        {line.taxTreatment === 'standard' && (
          <Form.Control
            size="sm"
            className="mt-1"
            type="number"
            min="0"
            step="0.0001"
            value={line.taxRate}
            placeholder="Tax rate %"
            onChange={(event) => onChange(line.key, { taxRate: event.target.value })}
          />
        )}
        <Form.Select
          size="sm"
          className="mt-1"
          value={line.inputTaxSource}
          onChange={(event) =>
            onChange(line.key, { inputTaxSource: event.target.value as InputTaxSource, inputTaxAmount: event.target.value === 'none' ? '0' : line.inputTaxAmount })
          }
        >
          <option value="none">No input tax</option>
          <option value="supplier">Supplier GST</option>
          <option value="customs">Customs GST</option>
          <option value="second_hand">Second-hand deduction</option>
        </Form.Select>
        {line.inputTaxSource !== 'none' && (
          <Form.Control
            size="sm"
            className="mt-1"
            type="number"
            min="0"
            step="0.0001"
            value={line.inputTaxAmount}
            placeholder="Input tax amount"
            onChange={(event) => onChange(line.key, { inputTaxAmount: event.target.value })}
          />
        )}
        {line.inputTaxSource !== 'none' && (
          <>
            <Form.Check
              className="mt-1"
              type="checkbox"
              label="Claim input tax"
              checked={line.claimInputTax}
              onChange={(event) =>
                onChange(line.key, {
                  claimInputTax: event.target.checked,
                  claimablePercentage: event.target.checked ? (line.claimablePercentage === '0' ? '100' : line.claimablePercentage) : '0'
                })
              }
            />
            {line.claimInputTax && (
              <Form.Control
                size="sm"
                type="number"
                min="0"
                max="100"
                step="0.0001"
                value={line.claimablePercentage}
                placeholder="Claimable %"
                onChange={(event) => onChange(line.key, { claimablePercentage: event.target.value })}
              />
            )}
            {line.claimInputTax && line.claimablePercentage !== '100' && (
              <Form.Control
                size="sm"
                className="mt-1"
                value={line.apportionmentBasis}
                placeholder="Apportionment basis"
                onChange={(event) => onChange(line.key, { apportionmentBasis: event.target.value })}
              />
            )}
          </>
        )}
        {Object.entries(errors)
          .filter(([field]) =>
            ['supplier_cost_incl_tax', 'tax_treatment', 'tax_rate', 'input_tax_source', 'input_tax_amount', 'claimable_percentage', 'apportionment_basis'].includes(field)
          )
          .map(([field, message]) => (
            <Form.Text key={field} className="text-danger d-block">
              {message}
            </Form.Text>
          ))}
      </td>
      <td>
        <Form.Control
          size="sm"
          value={line.supplierLotReference}
          placeholder="Supplier lot"
          onChange={(event) => onChange(line.key, { supplierLotReference: event.target.value })}
        />
        <Form.Control size="sm" className="mt-1" type="date" value={line.expiresOn} onChange={(event) => onChange(line.key, { expiresOn: event.target.value })} />
      </td>
      <td>
        <Button size="sm" variant="outline-danger" disabled={!removable} onClick={() => onRemove(line.key)} aria-label={`Remove line ${index + 1}`}>
          Remove
        </Button>
      </td>
    </tr>
  )
}

interface ReceiptEditorProps {
  receipt?: StockReceipt
  items: Array<InventoryItem>
  locations: Array<Location>
  suppliers: Array<Supplier>
  units: Array<InventoryUnit>
  seedTrayItemIds: Set<number>
  onClosed: () => void
}

// The parent mounts this with a key derived from which draft is open, so
// switching drafts re-initialises every field instead of syncing props in an
// effect.
function ReceiptEditor({ receipt, items, locations, suppliers, units, seedTrayItemIds, onClosed }: ReceiptEditorProps) {
  const queryClient = useQueryClient()
  const nextKey = React.useRef(0)

  function freshKey() {
    nextKey.current += 1
    return `new-${nextKey.current}`
  }

  const [receiptPk, setReceiptPk] = React.useState<number | null>(receipt?.pk ?? null)
  const [supplier, setSupplier] = React.useState<number | ''>(receipt?.supplier ?? '')
  const [receivedDate, setReceivedDate] = React.useState(receipt?.received_date ?? new Date().toISOString().slice(0, 10))
  const [supplierReference, setSupplierReference] = React.useState(receipt?.supplier_reference ?? '')
  const [invoiceDate, setInvoiceDate] = React.useState(receipt?.invoice_date ?? '')
  const [sourceDocumentType, setSourceDocumentType] = React.useState<ReceiptDocumentType>(receipt?.source_document_type ?? 'none')
  const [sourceDocumentNumber, setSourceDocumentNumber] = React.useState(receipt?.source_document_number ?? '')
  const [evidenceReference, setEvidenceReference] = React.useState(receipt?.evidence_reference ?? '')
  const [evidenceUrl, setEvidenceUrl] = React.useState(receipt?.evidence_url ?? '')
  const [currencyCode, setCurrencyCode] = React.useState(receipt?.currency_code ?? '')
  const [notes, setNotes] = React.useState(receipt?.notes ?? '')
  const [lines, setLines] = React.useState<Array<ReceiptLineDraft>>(() =>
    receipt && receipt.lines.length > 0 ? receipt.lines.map((line) => hydrateLine(line, `saved-${line.pk}`)) : [blankLine(freshKey())]
  )
  const [error, setError] = React.useState<string>()
  const [lineErrors, setLineErrors] = React.useState<Array<Record<string, string>>>([])
  const [saved, setSaved] = React.useState(false)
  const [taxWarnings, setTaxWarnings] = React.useState(receipt?.tax_warnings ?? [])

  const selectableItems = receivableItems(items, seedTrayItemIds)
  const selectableLocations = receivableLocations(locations)

  // Any edit that could change the arithmetic clears the normalization it was
  // based on. A stale number that still looks like a number is worse than none.
  function updateLine(key: string, patch: Partial<ReceiptLineDraft>, renormalize = false) {
    setSaved(false)
    setLines((current) => current.map((line) => (line.key === key ? { ...line, ...patch, ...(renormalize ? { baseQuantity: null, baseUnit: null } : {}) } : line)))
  }

  function addLine() {
    setSaved(false)
    setLines((current) => [...current, blankLine(freshKey())])
  }

  function removeLine(key: string) {
    setSaved(false)
    setLines((current) => current.filter((line) => line.key !== key))
  }

  function recordFailure(caught: unknown) {
    setSaved(false)
    if (caught instanceof ApiError) {
      const document = documentErrors(caught.body)
      setError(document.length > 0 ? document.join(' ') : caught.message)
      setLineErrors(lineFieldErrors(caught.body))
      return
    }
    setError(localErrorMessage(caught))
    setLineErrors([])
  }

  const saveMutation = useMutation({
    mutationFn: () => {
      const payload = {
        supplier: Number(supplier),
        received_date: receivedDate,
        supplier_reference: supplierReference,
        invoice_date: invoiceDate || null,
        source_document_type: sourceDocumentType,
        source_document_number: sourceDocumentNumber,
        evidence_reference: evidenceReference,
        evidence_url: evidenceUrl,
        notes,
        // Blank means "whatever the workspace says", which the server fills in
        // and returns, so these inputs populate themselves after the first save.
        ...(currencyCode.trim() === '' ? {} : { currency_code: currencyCode }),
        lines: lines.map(linePayload)
      }
      return receiptPk === null ? createStockReceipt(payload) : updateStockReceipt(receiptPk, payload)
    },
    onSuccess: async (response) => {
      setReceiptPk(response.pk)
      setCurrencyCode(response.currency_code)
      // A PATCH deletes and recreates every line, so the returned pks are all
      // new. Identity is carried positionally instead, which the server
      // guarantees by creating lines in the order they were submitted.
      setLines((current) => response.lines.map((line, index) => hydrateLine(line, current[index]?.key ?? `saved-${line.pk}`)))
      setTaxWarnings(response.tax_warnings)
      setError(undefined)
      setLineErrors([])
      setSaved(true)
      await invalidateReceipts(queryClient)
    },
    onError: recordFailure
  })

  const complete = lines.every((line) => line.item !== '' && line.destination !== '' && line.unitChoice !== '' && line.supplierCostInclTax.trim() !== '')
  const quantified = lines.every((line) => line.quantityCertainty === 'unknown' || line.quantity.trim() !== '')
  const readyToSave = supplier !== '' && receivedDate !== '' && lines.length > 0 && complete && quantified

  return (
    <Card className="mb-4">
      <Card.Body>
        <Card.Title>{receiptPk === null ? 'Receive inventory' : `Edit draft receipt #${receiptPk}`}</Card.Title>
        <Row className="g-2">
          <Col md={2}>
            <Form.Group className="mb-3" controlId="receipt-supplier">
              <Form.Label>Supplier</Form.Label>
              <Form.Select value={supplier} onChange={(event) => setSupplier(event.target.value === '' ? '' : Number(event.target.value))}>
                <option value="">Select a supplier</option>
                {suppliers.map((entry) => (
                  <option key={entry.pk} value={entry.pk}>
                    {entry.name}
                  </option>
                ))}
              </Form.Select>
            </Form.Group>
          </Col>
          <Col md={2}>
            <Form.Group className="mb-3" controlId="receipt-received-date">
              <Form.Label>Received</Form.Label>
              <Form.Control type="date" value={receivedDate} onChange={(event) => setReceivedDate(event.target.value)} />
            </Form.Group>
          </Col>
          <Col md={2}>
            <Form.Group className="mb-3" controlId="receipt-supplier-reference">
              <Form.Label>Supplier reference</Form.Label>
              <Form.Control value={supplierReference} placeholder="Invoice or docket number" onChange={(event) => setSupplierReference(event.target.value)} />
            </Form.Group>
          </Col>
          <Col md={2}>
            <Form.Group className="mb-3" controlId="receipt-currency">
              <Form.Label>Currency</Form.Label>
              <Form.Control value={currencyCode} placeholder="Workspace default" onChange={(event) => setCurrencyCode(event.target.value)} />
            </Form.Group>
          </Col>
          <Col md={2}>
            <Form.Group className="mb-3" controlId="receipt-invoice-date">
              <Form.Label>Invoice date</Form.Label>
              <Form.Control type="date" value={invoiceDate} onChange={(event) => setInvoiceDate(event.target.value)} />
            </Form.Group>
          </Col>
        </Row>
        <Row className="g-2">
          <Col md={3}>
            <Form.Group className="mb-3" controlId="receipt-source-document-type">
              <Form.Label>Source record</Form.Label>
              <Form.Select value={sourceDocumentType} onChange={(event) => setSourceDocumentType(event.target.value as ReceiptDocumentType)}>
                <option value="none">None recorded</option>
                <option value="taxable_supply">Taxable supply information</option>
                <option value="invoice">Invoice</option>
                <option value="receipt">Receipt</option>
                <option value="buyer_created">Buyer-created information</option>
                <option value="customs_entry">Customs entry or statement</option>
                <option value="contract">Contract or agreement</option>
                <option value="bank_record">Bank record</option>
                <option value="other">Other</option>
              </Form.Select>
            </Form.Group>
          </Col>
          <Col md={3}>
            <Form.Group className="mb-3" controlId="receipt-source-document-number">
              <Form.Label>Document number</Form.Label>
              <Form.Control value={sourceDocumentNumber} onChange={(event) => setSourceDocumentNumber(event.target.value)} />
            </Form.Group>
          </Col>
          <Col md={3}>
            <Form.Group className="mb-3" controlId="receipt-evidence-reference">
              <Form.Label>Evidence reference</Form.Label>
              <Form.Control value={evidenceReference} onChange={(event) => setEvidenceReference(event.target.value)} />
            </Form.Group>
          </Col>
          <Col md={3}>
            <Form.Group className="mb-3" controlId="receipt-evidence-url">
              <Form.Label>Evidence URL</Form.Label>
              <Form.Control type="url" value={evidenceUrl} onChange={(event) => setEvidenceUrl(event.target.value)} />
            </Form.Group>
          </Col>
          <Col md={12}>
            <Form.Group className="mb-3" controlId="receipt-notes">
              <Form.Label>Notes</Form.Label>
              <Form.Control value={notes} onChange={(event) => setNotes(event.target.value)} />
            </Form.Group>
          </Col>
        </Row>

        <Table size="sm" responsive>
          <thead>
            <tr>
              <th>Item</th>
              <th>Quantity</th>
              <th>Unit</th>
              <th>Normalizes to</th>
              <th>Destination</th>
              <th>Cost and input tax</th>
              <th>Lot reference and expiry</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {lines.map((line, index) => (
              <LineRow
                key={line.key}
                line={line}
                index={index}
                items={selectableItems}
                locations={selectableLocations}
                units={units}
                errors={lineErrors[index] ?? {}}
                removable={lines.length > 1}
                onChange={updateLine}
                onRemove={removeLine}
              />
            ))}
          </tbody>
        </Table>
        <Button size="sm" variant="outline-secondary" className="mb-3" onClick={addLine}>
          Add a line
        </Button>

        {error && <Alert variant="danger">{error}</Alert>}
        {taxWarnings.map((warning) => (
          <Alert key={`${warning.code}-${warning.line_id ?? 'receipt'}`} variant="warning" className="py-2">
            {warning.message}
          </Alert>
        ))}
        {saved && <Alert variant="info">Saved as a draft. Nothing has moved yet — check the normalized quantities, then post it from the list below.</Alert>}

        <div className="d-flex gap-2">
          <Button disabled={!readyToSave || saveMutation.isPending} onClick={() => saveMutation.mutate()}>
            {saveMutation.isPending ? 'Saving…' : receiptPk === null ? 'Save draft' : 'Save changes'}
          </Button>
          <Button variant="outline-secondary" onClick={onClosed}>
            Close
          </Button>
        </div>
      </Card.Body>
    </Card>
  )
}

export { ReceiptEditor }
