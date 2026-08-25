import { AttachmentArchiveReport, AttachmentTargetType, AttachmentUploadResult, ImageAttachment } from '../types/attachments'
import { csrfPostForm, fetchAsJson } from '../utils'

function getAttachments(targetType: AttachmentTargetType, targetId: number, signal?: AbortSignal): Promise<Array<ImageAttachment>> {
  const query = new URLSearchParams({ target_type: targetType, target_id: String(targetId) })
  return fetchAsJson<Array<ImageAttachment>>('/attachments/?' + query.toString(), signal)
}

async function uploadAttachments(targetType: AttachmentTargetType, targetId: number, files: Array<File>): Promise<AttachmentUploadResult> {
  const uploaded: Array<ImageAttachment> = []
  const failures: AttachmentUploadResult['failures'] = []
  for (const file of files) {
    const form = new FormData()
    form.set('target_type', targetType)
    form.set('target_id', String(targetId))
    form.set('image', file)
    try {
      const response = await csrfPostForm('/attachments/', form)
      uploaded.push((await response.json()) as ImageAttachment)
    } catch (error) {
      failures.push({ file, message: error instanceof Error ? error.message : String(error) })
    }
  }
  return { uploaded, failures }
}

async function restoreAttachmentArchive(archive: File, dryRun: boolean): Promise<AttachmentArchiveReport> {
  const form = new FormData()
  form.set('archive', archive)
  form.set('dry_run', String(dryRun))
  const response = await csrfPostForm('/attachments/archive/restore/', form)
  return response.json() as Promise<AttachmentArchiveReport>
}

export { getAttachments, restoreAttachmentArchive, uploadAttachments }
