import { csrfPatch, csrfPost, fetchAsJson } from '../utils'
import { WorkAssignee, WorkFilters, WorkRule, WorkTask } from '../types/work'

function getWorkTasks(filters: WorkFilters, signal?: AbortSignal): Promise<Array<WorkTask>> {
  const query = new URLSearchParams({ view: filters.view })
  if (filters.task_type) query.set('task_type', filters.task_type)
  if (filters.priority !== undefined) query.set('priority', String(filters.priority))
  if (filters.assignee !== undefined) query.set('assignee', String(filters.assignee))
  if (filters.batch !== undefined) query.set('batch', String(filters.batch))
  if (filters.location !== undefined) query.set('location', String(filters.location))
  return fetchAsJson<Array<WorkTask>>(`/work/tasks/?${query.toString()}`, signal)
}

function getWorkRules(signal?: AbortSignal): Promise<Array<WorkRule>> {
  return fetchAsJson<Array<WorkRule>>('/work/rules/', signal)
}

function getWorkAssignees(signal?: AbortSignal): Promise<Array<WorkAssignee>> {
  return fetchAsJson<Array<WorkAssignee>>('/work/assignees/', signal)
}

function addWorkTask(data: object): Promise<WorkTask> {
  return csrfPost('/work/tasks/', data).then((response) => response.json() as Promise<WorkTask>)
}

function acknowledgeWorkTask(key: string): Promise<WorkTask> {
  return csrfPost('/work/tasks/acknowledge/', { key }).then((response) => response.json() as Promise<WorkTask>)
}

function actOnWorkTask(pk: number, data: object): Promise<WorkTask> {
  return csrfPost(`/work/tasks/${pk}/act/`, data).then((response) => response.json() as Promise<WorkTask>)
}

function addWorkRule(data: object): Promise<WorkRule> {
  return csrfPost('/work/rules/', data).then((response) => response.json() as Promise<WorkRule>)
}

function updateWorkRule(pk: number, data: object): Promise<WorkRule> {
  return csrfPatch(`/work/rules/${pk}/`, data).then((response) => response.json() as Promise<WorkRule>)
}

export { acknowledgeWorkTask, actOnWorkTask, addWorkRule, addWorkTask, getWorkAssignees, getWorkRules, getWorkTasks, updateWorkRule }
