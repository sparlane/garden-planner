import React from 'react'
import { Form } from 'react-bootstrap'

import { MaturityBasis, Plant, PlantVariety, PlantingDetails } from '../types/plants'

// The figures a crop and its varieties are planned by, written once because
// three screens read them in three directions: the catalog table lists them
// across a row, a crop's page asks how many of its varieties are planned by
// each one, and a variety's page asks which of them it says anything about at
// all. A list written out per screen is a column that quietly stops matching
// its heading.
//
// The two-field ranges are one figure with two boxes rather than two figures.
// A reference set supplies both halves or neither, and a gardener leaving
// "germination" blank means the pair, so anything counting what a record says
// has to count them together or report a crop twice.
interface CultivationFigure {
  field: keyof PlantingDetails
  maximumField?: keyof PlantingDetails
  label: string
}

const CULTIVATION_FIGURES: ReadonlyArray<CultivationFigure> = [
  { field: 'spacing', label: 'Spacing (mm)' },
  { field: 'inter_row_spacing', label: 'Row spacing (mm)' },
  { field: 'plants_per_square_foot', label: 'Plants per sq ft' },
  { field: 'germination_days_min', maximumField: 'germination_days_max', label: 'Germination (days)' },
  { field: 'maturity_days_min', maximumField: 'maturity_days_max', label: 'Maturity (days)' }
]

const BASIS_LABELS: Record<MaturityBasis, string> = {
  seed: 'From seed',
  transplanting: 'From transplanting'
}

// Every figure a form writes, under the names the API calls them. One
// vocabulary rather than two, so a field cannot be added to the payload and
// missed on the control that fills it.
type CultivationField = keyof PlantingDetails

type CultivationFormState = Record<CultivationField, string> & { name: string; notes: string }

function optionalNumber(value: string): number | null {
  return value === '' ? null : Number(value)
}

function inputNumber(value: number | null | undefined): string {
  return value === null || value === undefined ? '' : String(value)
}

function displayNumber(value: number | null | undefined): string {
  return value === null || value === undefined ? '—' : String(value)
}

function displayRange(minimum: number | null | undefined, maximum: number | null | undefined): string {
  if (minimum == null && maximum == null) return '—'
  if (minimum == null) return `Up to ${maximum}`
  if (maximum == null) return `${minimum}+`
  return `${minimum}–${maximum}`
}

function cultivationState(value?: Plant | PlantVariety): CultivationFormState {
  const state = { name: value?.name ?? '', notes: value?.notes ?? '' } as CultivationFormState
  for (const figure of CULTIVATION_FIGURES) {
    state[figure.field] = inputNumber(value?.[figure.field])
    if (figure.maximumField) state[figure.maximumField] = inputNumber(value?.[figure.maximumField])
  }
  return state
}

function cultivationPayload(form: CultivationFormState): PlantingDetails & { name: string; notes: string } {
  const payload: PlantingDetails & { name: string; notes: string } = { name: form.name, notes: form.notes }
  for (const figure of CULTIVATION_FIGURES) {
    payload[figure.field] = optionalNumber(form[figure.field])
    if (figure.maximumField) payload[figure.maximumField] = optionalNumber(form[figure.maximumField])
  }
  return payload
}

// Whether a record says anything about one figure, counting a range as the one
// figure it is. This is what a blank box means, and the question both
// directions of guidance are asked of: a variety with nothing here is planned
// by its crop, and a crop with something here is what plans them.
function figureIsSet(record: PlantingDetails, figure: CultivationFigure): boolean {
  if (record[figure.field] != null) return true
  return figure.maximumField !== undefined && record[figure.maximumField] != null
}

function figureText(record: PlantingDetails, figure: CultivationFigure): string {
  if (figure.maximumField) return displayRange(record[figure.field], record[figure.maximumField])
  return displayNumber(record[figure.field])
}

function formIsSet(form: CultivationFormState, figure: CultivationFigure): boolean {
  if (form[figure.field] !== '') return true
  return figure.maximumField !== undefined && form[figure.maximumField] !== ''
}

// What one figure of a variety is in force as, and whose figure it is. Read
// from the server's answer rather than worked out here, because a blank figure
// and an override that happens to equal the crop's look identical from the
// variety alone -- and because the figure a sowing date is counted from has to
// be the one the screen shows.
//
// A range is resolved half by half and reported as one figure, because that is
// what a gardener states or leaves alone.
function effectiveFigure(variety: PlantVariety, figure: CultivationFigure): { text: string; inherited: boolean; stated: boolean } {
  const minimum = variety.effective[figure.field]
  const maximum = figure.maximumField ? variety.effective[figure.maximumField] : undefined
  const text = maximum ? displayRange(minimum.value, maximum.value) : displayNumber(minimum.value)
  return { text, inherited: minimum.inherited && (maximum?.inherited ?? true), stated: text !== '\u2014' }
}

// An inherited figure in parentheses, because the cell beside it may already
// be grey for having arrived with a reference set, and the two say different
// things: one is a figure printed for somebody else's climate, the other is
// this garden's own figure recorded one level up.
function EffectiveFigureCell({ variety, figure, crop }: { variety: PlantVariety; figure: CultivationFigure; crop: string }) {
  const { text, inherited, stated } = effectiveFigure(variety, figure)
  if (!inherited || !stated) return <>{text}</>
  return (
    <span className="text-body-secondary" title={`Left blank, so ${crop}'s figure is what this variety is planned by`}>
      ({text})
    </span>
  )
}

// Said under the box the figure is typed into, because that is where a
// gardener decides whether to leave it blank -- and a blank box is the one
// control on the screen whose meaning is not written on it. It reports what
// the crop says and what this box currently does about it, so the note follows
// the typing rather than the last save.
function CropFigureNote({ figure, crop, form }: { figure: CultivationFigure; crop: Plant | undefined; form: CultivationFormState }) {
  if (!crop) return null
  const overriding = formIsSet(form, figure)
  const cropSays = figureIsSet(crop, figure)
  if (!cropSays) {
    return <Form.Text className="text-body-secondary">{crop.name} does not say either.</Form.Text>
  }
  return (
    <Form.Text className="text-body-secondary">
      {overriding ? `Overrides ${crop.name}: ${figureText(crop, figure)}.` : `Blank, so ${crop.name}'s ${figureText(crop, figure)} is what this variety is planned by.`}
    </Form.Text>
  )
}

// The same sentence read from the crop's end: correcting a figure here moves
// every variety that leaves it blank, which is the whole reason a variety is
// allowed to. It counts rather than naming one figure, because the maturity
// basis is inherited the same way and is not a number.
function InheritingVarietiesNote({ inheriting, total }: { inheriting: number; total: number }) {
  if (total === 0) return null
  if (inheriting === 0) {
    return <Form.Text className="text-body-secondary">Every variety of this crop says its own.</Form.Text>
  }
  return (
    <Form.Text className="text-body-secondary">
      {inheriting === total ? `All ${total} ${total === 1 ? 'variety is' : 'varieties are'} planned by this.` : `${inheriting} of ${total} varieties are planned by this.`}
    </Form.Text>
  )
}

export {
  BASIS_LABELS,
  CULTIVATION_FIGURES,
  CropFigureNote,
  CultivationFigure,
  CultivationFormState,
  EffectiveFigureCell,
  InheritingVarietiesNote,
  cultivationPayload,
  cultivationState,
  effectiveFigure,
  figureIsSet,
  figureText
}
