type AttachmentTargetType = 'plant' | 'nursery_observation' | 'health_observation' | 'harvest'

interface ImageAttachment {
  id: string
  target_type: AttachmentTargetType
  target_id: number
  original_filename: string
  content_type: 'image/jpeg' | 'image/png'
  byte_size: number
  width: number
  height: number
  sha256: string
  captured_at: string | null
  created: string
  content_url: string
  thumbnail_url: string
}

interface AttachmentUploadFailure {
  file: File
  message: string
}

interface AttachmentUploadResult {
  uploaded: Array<ImageAttachment>
  failures: Array<AttachmentUploadFailure>
}

interface AttachmentArchiveReport {
  valid: boolean
  would_create: number
  already_present: number
  created?: number
  errors: Array<string>
}

export type { AttachmentArchiveReport, AttachmentTargetType, AttachmentUploadFailure, AttachmentUploadResult, ImageAttachment }
