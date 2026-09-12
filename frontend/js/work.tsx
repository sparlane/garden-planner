import React from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Alert, Badge, Button, Card, Col, Form, Nav, Row, Table } from 'react-bootstrap'
import { NavLink } from 'react-router'

import { acknowledgeWorkTask, actOnWorkTask, addWorkRule, addWorkTask, getWorkAssignees, getWorkRules, getWorkTasks, updateWorkRule } from './api/work'
import { CatalogSearch, DuplicateWarning, MergeDialog, MergedIntoNote, RetireButton, RetiredBadge, mergeChoices, retiredRowClass } from './catalog'
import { queryKeys, searchedKey } from './query'
import { CatalogRecordLabel } from './types/catalog'
import { WorkFilters, WorkRule, WorkTask, WorkView } from './types/work'
import { errorsByField, formatDateTime, localDatetimeInputValue, parseLocalDatetimeInput } from './utils'

const TASK_TYPES = [
  ['germination_check', 'Germination check'],
  ['germination_assessment', 'Germination assessment'],
  ['watering', 'Watering'],
  ['feeding', 'Feeding'],
  ['thinning', 'Thinning'],
  ['spacing', 'Spacing'],
  ['potting_on', 'Potting on'],
  ['hardening', 'Hardening'],
  ['ready_review', 'Ready-date review'],
  ['harvest_review', 'Harvest review'],
  ['stocktake', 'Stocktake'],
  ['order_picking', 'Order picking'],
  ['stage_review', 'Stage review'],
  ['health_inspection', 'Health inspection'],
  ['treatment_follow_up', 'Treatment follow-up'],
  ['reservation_review', 'Reservation review'],
  ['assumption_review', 'Planning assumption review'],
  ['custom', 'Custom']
]

// What a rule counts from. Everything but the calendar counts from a fact the
// workspace already records, which is why those rules arrive seeded rather than
// being typed here; all of them are shown, because a rule that counts from the
// wrong fact is corrected by saying so.
const TRIGGERS = [
  ['sowing_germination', 'Expected sowing germination'],
  ['sowing_germination_end', 'End of sowing germination window'],
  ['plan_milestone', 'Approved plan milestone'],
  ['stage_age', 'Current stage target age'],
  ['expected_ready', 'Recorded expected-ready date'],
  ['sowing_maturity', 'Expected sowing maturity'],
  ['calendar', 'Recurring calendar work'],
  ['health_follow_up', 'Health follow-up due'],
  ['reservation_expiry', 'Sales reservation expiry'],
  ['assumption_variance', 'Diverged planning assumption']
]

const VIEWS: Array<[WorkView, string]> = [
  ['today', 'Today'],
  ['week', 'This week'],
  ['overdue', 'Overdue'],
  ['snoozed', 'Snoozed'],
  ['completed', 'Completed']
]

function ManualTaskForm() {
  const cache = useQueryClient()
  const [title, setTitle] = React.useState('')
  const [taskType, setTaskType] = React.useState('custom')
  const [dueStart, setDueStart] = React.useState(localDatetimeInputValue())
  const [dueEnd, setDueEnd] = React.useState(localDatetimeInputValue(new Date(Date.now() + 60 * 60 * 1000)))
  const [frequency, setFrequency] = React.useState('')
  const mutation = useMutation({
    mutationFn: addWorkTask,
    onSuccess: () => {
      setTitle('')
      cache.invalidateQueries({ queryKey: queryKeys.work.all })
    }
  })
  const submit = (event: React.FormEvent) => {
    event.preventDefault()
    const start = parseLocalDatetimeInput(dueStart)
    const end = parseLocalDatetimeInput(dueEnd)
    if (!start || !end) return
    mutation.mutate({
      title,
      task_type: taskType,
      due_start: start.toISOString(),
      due_end: end.toISOString(),
      recurrence: frequency ? { frequency, interval: 1, weekdays: frequency === 'weekly' ? [new Date(start).getDay() === 0 ? 6 : new Date(start).getDay() - 1] : [] } : {}
    })
  }
  return (
    <Card className="mb-3">
      <Card.Header>Schedule manual work</Card.Header>
      <Card.Body>
        <Form onSubmit={submit}>
          <Row className="g-2">
            <Col md={4}>
              <Form.Control required placeholder="Work to do" value={title} onChange={(event) => setTitle(event.target.value)} />
            </Col>
            <Col md={2}>
              <Form.Select value={taskType} onChange={(event) => setTaskType(event.target.value)}>
                {TASK_TYPES.map(([value, label]) => (
                  <option key={value} value={value}>
                    {label}
                  </option>
                ))}
              </Form.Select>
            </Col>
            <Col md={2}>
              <Form.Control required type="datetime-local" value={dueStart} onChange={(event) => setDueStart(event.target.value)} />
            </Col>
            <Col md={2}>
              <Form.Control required type="datetime-local" value={dueEnd} onChange={(event) => setDueEnd(event.target.value)} />
            </Col>
            <Col md={1}>
              <Form.Select aria-label="Recurrence" value={frequency} onChange={(event) => setFrequency(event.target.value)}>
                <option value="">Once</option>
                <option value="daily">Daily</option>
                <option value="weekly">Weekly</option>
              </Form.Select>
            </Col>
            <Col md={1}>
              <Button type="submit" disabled={mutation.isPending}>
                Add
              </Button>
            </Col>
          </Row>
        </Form>
      </Card.Body>
    </Card>
  )
}

function TaskActions({ task }: { task: WorkTask }) {
  const cache = useQueryClient()
  const assignees = useQuery({ queryKey: queryKeys.work.assignees, queryFn: ({ signal }) => getWorkAssignees(signal) })
  const mutation = useMutation({
    mutationFn: async (data: Record<string, unknown>) => {
      const acknowledged = task.pk === null ? await acknowledgeWorkTask(task.key) : task
      return actOnWorkTask(acknowledged.pk as number, { ...data, idempotency_key: crypto.randomUUID() })
    },
    onSuccess: () => cache.invalidateQueries({ queryKey: queryKeys.work.all })
  })
  const reasoned = (action: 'skip' | 'reopen') => {
    const reason = globalThis.prompt(`Reason to ${action} this task:`)
    if (reason) mutation.mutate({ action, reason })
  }
  if (task.status === 'completed' || task.status === 'skipped') {
    return (
      <Button size="sm" variant="outline-secondary" onClick={() => reasoned('reopen')}>
        Reopen
      </Button>
    )
  }
  return (
    <div className="d-flex flex-wrap gap-1">
      <Button size="sm" onClick={() => mutation.mutate({ action: 'claim' })}>
        Claim
      </Button>
      <Form.Select
        size="sm"
        aria-label={`Assign ${task.title}`}
        value={task.assignee ?? ''}
        onChange={(event) => mutation.mutate({ action: 'assign', assignee: event.target.value ? Number(event.target.value) : null })}
      >
        <option value="">Unassigned</option>
        {(assignees.data ?? []).map((user) => (
          <option key={user.pk} value={user.pk}>
            {user.username}
          </option>
        ))}
      </Form.Select>
      <Button size="sm" variant="outline-secondary" onClick={() => mutation.mutate({ action: 'snooze', until: new Date(Date.now() + 24 * 60 * 60 * 1000).toISOString() })}>
        Snooze 1 day
      </Button>
      <Button size="sm" variant="success" onClick={() => mutation.mutate({ action: 'complete' })}>
        Complete
      </Button>
      <Button size="sm" variant="outline-danger" onClick={() => reasoned('skip')}>
        Skip
      </Button>
    </div>
  )
}

function TaskTable({ tasks }: { tasks: Array<WorkTask> }) {
  return (
    <Table responsive hover>
      <thead>
        <tr>
          <th>Due</th>
          <th>Work</th>
          <th>Targets</th>
          <th>Assignee</th>
          <th>Priority</th>
          <th>Actions</th>
        </tr>
      </thead>
      <tbody>
        {tasks.map((task) => (
          <tr key={task.key}>
            <td>
              {formatDateTime(task.due_start)}
              <div className="small text-muted">to {formatDateTime(task.due_end)}</div>
            </td>
            <td>
              <strong>{task.title}</strong>
              <div>
                <Badge bg={task.origin === 'generated' ? 'info' : 'secondary'}>{task.origin}</Badge> {task.task_type.replaceAll('_', ' ')}
              </div>
              {task.notes && <div>{task.notes}</div>}
              {task.history.length > 0 && (
                <details className="mt-1">
                  <summary>History ({task.history.length})</summary>
                  {task.history.map((entry) => (
                    <div className="small" key={entry.pk}>
                      {formatDateTime(entry.created)} · {entry.action} · {entry.actor_name ?? 'system'}
                      {entry.reason ? ` — ${entry.reason}` : ''}
                    </div>
                  ))}
                </details>
              )}
            </td>
            <td>
              {task.links
                .filter((link) => link.role === 'target')
                .map((link) => (
                  <div key={`${link.target_type}:${link.object_id}`}>
                    {link.url ? <NavLink to={link.url}>{link.label}</NavLink> : link.label}
                    {link.active_health_alerts > 0 && (
                      <Badge bg="warning" text="dark" className="ms-1">
                        Health alert
                      </Badge>
                    )}
                  </div>
                ))}
            </td>
            <td>{task.assignee_name ?? 'Unassigned'}</td>
            <td>{task.priority}</td>
            <td>
              <TaskActions task={task} />
            </td>
          </tr>
        ))}
        {tasks.length === 0 && (
          <tr>
            <td colSpan={6} className="text-muted">
              No work in this view.
            </td>
          </tr>
        )}
      </tbody>
    </Table>
  )
}

interface Merging {
  source: CatalogRecordLabel
  choices: Array<CatalogRecordLabel>
}

const RULES_COLLECTION = '/work/rules/'

// A rule added here watches the calendar, because every other trigger counts
// from a fact the workspace already records and arrives seeded. It is still
// shown and still editable on the row: what a rule counts from is half of what
// files it, so a merge refuses to change it and this is where that edit is
// made.
const NEW_RULE_TRIGGER = 'calendar'

function nameOf(rules: Array<WorkRule>, pk: number | null): string | null {
  if (pk == null) return null
  return rules.find((rule) => rule.pk === pk)?.name ?? null
}

// A merge is onto another rule making the same kind of work from the same
// anchor, because the server refuses anything else as a reclassification and a
// picker offering one would make that refusal the operator's problem rather
// than the screen's.
function ruleMergeChoices(rules: Array<WorkRule>, source: WorkRule): Array<CatalogRecordLabel> {
  return mergeChoices(
    rules.filter((rule) => rule.task_type === source.task_type && rule.trigger === source.trigger),
    source
  )
}

function RuleEditor() {
  const cache = useQueryClient()
  const [code, setCode] = React.useState('')
  const [name, setName] = React.useState('')
  const [taskType, setTaskType] = React.useState('watering')
  const [frequency, setFrequency] = React.useState<'daily' | 'weekly'>('daily')
  const [search, setSearch] = React.useState('')
  const [showRetired, setShowRetired] = React.useState(false)
  const [merging, setMerging] = React.useState<Merging | null>(null)
  const [failure, setFailure] = React.useState<string | null>(null)
  // The whole collection is what a merge may be onto and what the `Merged into`
  // note reads a name out of, both of which have to reach rules the search is
  // deliberately hiding; the rows read the searched list.
  const { data: rules = [] } = useQuery({ queryKey: queryKeys.work.rules, queryFn: ({ signal }) => getWorkRules(signal) })
  const { data: found = [] } = useQuery({ queryKey: searchedKey(queryKeys.work.rules, search), queryFn: ({ signal }) => getWorkRules(signal, search) })
  // Retiring a rule changes what the queue projects and merging one moves the
  // tasks already acknowledged under it, so what has moved on is the whole of
  // the work screen rather than this list.
  const refresh = () => cache.invalidateQueries({ queryKey: queryKeys.work.all })

  // Retirement is refused while a catalog rule stands in the way, a stable code
  // is refused outright, and so is a recurrence a trigger cannot carry, so a
  // failure names a field and belongs on the screen.
  async function save(write: () => Promise<unknown>) {
    setFailure(null)
    try {
      await write()
      await refresh()
    } catch (error) {
      const fields = errorsByField(error)
      setFailure(
        fields.active ?? fields.code ?? fields.name ?? fields.task_type ?? fields.trigger ?? fields.frequency ?? fields.weekdays ?? fields.form ?? 'The change could not be saved.'
      )
    }
  }

  const create = useMutation({
    mutationFn: () =>
      addWorkRule({
        code,
        name,
        task_type: taskType,
        trigger: NEW_RULE_TRIGGER,
        display_order: rules.length,
        frequency,
        interval: 1,
        weekdays: frequency === 'weekly' ? [0] : [],
        priority: 20,
        due_start_offset_days: 0,
        due_end_offset_days: 0,
        local_due_time: '09:00'
      })
  })
  const edit = useMutation({ mutationFn: ({ pk, changes }: { pk: number; changes: Partial<WorkRule> }) => updateWorkRule(pk, changes) })
  const visible = found.filter((rule) => rule.active || showRetired)

  return (
    <Card className="mt-4">
      <Card.Header className="d-flex justify-content-between align-items-center gap-3">
        <span>Automation rules</span>
        <div className="d-flex align-items-center gap-3">
          <CatalogSearch id="work-rule-search" onSearch={setSearch} label="Search care rules" />
          <Form.Check type="switch" id="show-retired-rules" label="Show retired" checked={showRetired} onChange={(event) => setShowRetired(event.target.checked)} />
        </div>
      </Card.Header>
      <Card.Body>
        {failure && (
          <Alert variant="danger" onClose={() => setFailure(null)} dismissible>
            {failure}
          </Alert>
        )}
        {merging && (
          <MergeDialog
            collection={RULES_COLLECTION}
            source={merging.source}
            choices={merging.choices}
            onMerged={() => {
              setMerging(null)
              void refresh()
            }}
            onCancel={() => setMerging(null)}
          />
        )}
        <Table size="sm">
          <thead>
            <tr>
              <th>Rule</th>
              <th>Work</th>
              <th>Trigger</th>
              <th>Recurrence</th>
              <th>Code</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {visible.map((rule) => (
              <tr key={rule.pk} className={retiredRowClass(rule.active)}>
                <td>
                  <Form.Control
                    aria-label={`Name of ${rule.code}`}
                    defaultValue={rule.name}
                    onBlur={(event) => {
                      const next = event.target.value.trim()
                      if (next && next !== rule.name) void save(() => edit.mutateAsync({ pk: rule.pk, changes: { name: next } }))
                    }}
                  />
                  <RetiredBadge active={rule.active} />
                  <MergedIntoNote into={nameOf(rules, rule.merged_into)} />
                </td>
                {/* The kind of work and the anchor file a rule the way a
                    category files a diagnosis, so both stay editable: a rule
                    making the wrong kind of work is refiled, which is the
                    deliberate edit a merge refuses to make on its own. */}
                <td>
                  <Form.Select
                    aria-label={`Work made by ${rule.name}`}
                    value={rule.task_type}
                    onChange={(event) => {
                      const next = event.target.value
                      if (next !== rule.task_type) void save(() => edit.mutateAsync({ pk: rule.pk, changes: { task_type: next } }))
                    }}
                  >
                    {TASK_TYPES.map(([value, label]) => (
                      <option key={value} value={value}>
                        {label}
                      </option>
                    ))}
                  </Form.Select>
                </td>
                <td>
                  <Form.Select
                    aria-label={`Trigger of ${rule.name}`}
                    value={rule.trigger}
                    onChange={(event) => {
                      const next = event.target.value
                      if (next !== rule.trigger) void save(() => edit.mutateAsync({ pk: rule.pk, changes: { trigger: next } }))
                    }}
                  >
                    {TRIGGERS.map(([value, label]) => (
                      <option key={value} value={value}>
                        {label}
                      </option>
                    ))}
                  </Form.Select>
                </td>
                <td className="text-nowrap">{rule.frequency || 'From source date'}</td>
                {/* The code is what the seeded defaults and every acknowledged
                    task hold this rule by, so it is shown and never offered for
                    editing: a wrong one is merged away instead. */}
                <td>
                  <code>{rule.code}</code>
                </td>
                <td className="text-nowrap">
                  {rule.merged_into === null && (
                    <>
                      <Button
                        size="sm"
                        variant="outline-secondary"
                        onClick={() => setMerging({ source: { pk: rule.pk, label: rule.name }, choices: ruleMergeChoices(rules, rule) })}
                      >
                        Merge
                      </Button>{' '}
                    </>
                  )}
                  <RetireButton active={rule.active} saving={edit.isPending} onChange={(active) => void save(() => edit.mutateAsync({ pk: rule.pk, changes: { active } }))} />
                </td>
              </tr>
            ))}
          </tbody>
        </Table>
        <Row className="g-2">
          <Col>
            <Form.Control aria-label="New care rule code" placeholder="stable_code" value={code} onChange={(event) => setCode(event.target.value)} />
          </Col>
          <Col>
            <Form.Control aria-label="New care rule name" placeholder="New care rule" value={name} onChange={(event) => setName(event.target.value)} />
            {/* Checked among the rules making the same kind of work from the
                same anchor, because that is where a merge would be allowed. */}
            <DuplicateWarning collection={RULES_COLLECTION} name={name} scope={{ task_type: taskType, trigger: NEW_RULE_TRIGGER }} />
          </Col>
          <Col>
            <Form.Select aria-label="New care rule work" value={taskType} onChange={(event) => setTaskType(event.target.value)}>
              {TASK_TYPES.map(([value, label]) => (
                <option key={value} value={value}>
                  {label}
                </option>
              ))}
            </Form.Select>
          </Col>
          <Col>
            <Form.Select aria-label="New care rule recurrence" value={frequency} onChange={(event) => setFrequency(event.target.value as 'daily' | 'weekly')}>
              <option value="daily">Daily</option>
              <option value="weekly">Weekly on Monday</option>
            </Form.Select>
          </Col>
          <Col xs="auto">
            <Button
              disabled={!code || !name || create.isPending}
              onClick={() =>
                void save(async () => {
                  await create.mutateAsync()
                  setCode('')
                  setName('')
                })
              }
            >
              Add rule
            </Button>
          </Col>
        </Row>
      </Card.Body>
    </Card>
  )
}

function WorkQueueView() {
  const [view, setView] = React.useState<WorkView>('today')
  const [taskType, setTaskType] = React.useState('')
  const [priority, setPriority] = React.useState('')
  const [assignee, setAssignee] = React.useState('')
  const [batch, setBatch] = React.useState('')
  const [location, setLocation] = React.useState('')
  const filters: WorkFilters = {
    view,
    task_type: taskType || undefined,
    priority: priority ? Number(priority) : undefined,
    assignee: assignee ? Number(assignee) : undefined,
    batch: batch ? Number(batch) : undefined,
    location: location ? Number(location) : undefined
  }
  const tasks = useQuery({ queryKey: queryKeys.work.tasks(filters), queryFn: ({ signal }) => getWorkTasks(filters, signal) })
  const assignees = useQuery({ queryKey: queryKeys.work.assignees, queryFn: ({ signal }) => getWorkAssignees(signal) })
  return (
    <main className="container-fluid py-3">
      <h1>Nursery work</h1>
      <p>Generated and manually scheduled work stays here until it is completed or deliberately skipped.</p>
      <ManualTaskForm />
      <Nav variant="tabs" activeKey={view} onSelect={(key) => key && setView(key as WorkView)}>
        {VIEWS.map(([value, label]) => (
          <Nav.Item key={value}>
            <Nav.Link eventKey={value}>{label}</Nav.Link>
          </Nav.Item>
        ))}
      </Nav>
      <Row className="g-2 py-3">
        <Col md={3}>
          <Form.Select aria-label="Task type" value={taskType} onChange={(event) => setTaskType(event.target.value)}>
            <option value="">All task types</option>
            {TASK_TYPES.map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </Form.Select>
        </Col>
        <Col md={2}>
          <Form.Control aria-label="Batch ID" type="number" min={1} placeholder="Batch ID" value={batch} onChange={(event) => setBatch(event.target.value)} />
        </Col>
        <Col md={2}>
          <Form.Control aria-label="Location ID" type="number" min={1} placeholder="Location ID" value={location} onChange={(event) => setLocation(event.target.value)} />
        </Col>
        <Col md={2}>
          <Form.Control aria-label="Priority" type="number" placeholder="Priority" value={priority} onChange={(event) => setPriority(event.target.value)} />
        </Col>
        <Col md={3}>
          <Form.Select aria-label="Assignee" value={assignee} onChange={(event) => setAssignee(event.target.value)}>
            <option value="">All assignees</option>
            {(assignees.data ?? []).map((user) => (
              <option key={user.pk} value={user.pk}>
                {user.username}
              </option>
            ))}
          </Form.Select>
        </Col>
      </Row>
      <TaskTable tasks={tasks.data ?? []} />
      <RuleEditor />
    </main>
  )
}

export { WorkQueueView }
