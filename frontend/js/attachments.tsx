import React from 'react'
import { Alert, Button, Form } from 'react-bootstrap'

import { uploadAttachments } from './api/attachments'
import { AttachmentTargetType, ImageAttachment } from './types/attachments'

interface PhotoInputProps {
  id: string
  files: Array<File>
  onChange: (files: Array<File>) => void
  label?: string
}

function FilePreview({ file }: { file: File }) {
  const url = React.useMemo(() => URL.createObjectURL(file), [file])
  React.useEffect(() => () => URL.revokeObjectURL(url), [url])
  return <img src={url} alt="" className="rounded border" style={{ width: 72, height: 72, objectFit: 'cover' }} />
}

function PhotoInput({ id, files, onChange, label = 'Photos (optional)' }: PhotoInputProps) {
  return (
    <Form.Group controlId={id}>
      <Form.Label>{label}</Form.Label>
      <Form.Control
        type="file"
        accept="image/jpeg,image/png,image/webp"
        capture="environment"
        multiple
        onChange={(event: React.ChangeEvent<HTMLInputElement>) => onChange(Array.from(event.target.files ?? []))}
      />
      <Form.Text>JPEG, PNG, or WebP; up to 15 MiB each. Location and camera metadata are removed.</Form.Text>
      {files.length > 0 && (
        <div className="d-flex flex-wrap gap-2 mt-2" aria-live="polite">
          {files.map((file, index) => (
            <div key={[file.name, file.lastModified, index].join(':')} className="text-center small" style={{ maxWidth: 88 }}>
              <FilePreview file={file} />
              <div className="text-truncate" title={file.name}>
                {file.name}
              </div>
            </div>
          ))}
        </div>
      )}
    </Form.Group>
  )
}

function AttachmentGallery({ attachments }: { attachments: Array<ImageAttachment> }) {
  if (attachments.length === 0) return null
  return (
    <div className="d-flex flex-wrap gap-2 mt-2">
      {attachments.map((attachment) => (
        <a key={attachment.id} href={attachment.content_url} target="_blank" rel="noreferrer" title={attachment.original_filename}>
          <img src={attachment.thumbnail_url} alt={attachment.original_filename} className="rounded border" loading="lazy" style={{ width: 88, height: 88, objectFit: 'cover' }} />
        </a>
      ))}
    </div>
  )
}

interface AttachmentUploaderProps {
  targetType: AttachmentTargetType
  targetId: number
  onUploaded?: (attachments: Array<ImageAttachment>) => void
  id: string
}

function AttachmentUploader({ targetType, targetId, onUploaded, id }: AttachmentUploaderProps) {
  const [files, setFiles] = React.useState<Array<File>>([])
  const [uploading, setUploading] = React.useState(false)
  const [error, setError] = React.useState('')

  async function upload() {
    setUploading(true)
    setError('')
    const result = await uploadAttachments(targetType, targetId, files)
    setFiles(result.failures.map((failure) => failure.file))
    if (result.failures.length > 0) {
      const noun = result.failures.length === 1 ? 'photo' : 'photos'
      setError(String(result.failures.length) + ' ' + noun + ' could not be uploaded. The failed selection is ready to retry.')
    }
    if (result.uploaded.length > 0) onUploaded?.(result.uploaded)
    setUploading(false)
  }

  return (
    <div>
      <PhotoInput id={id} files={files} onChange={setFiles} label="Add photos" />
      <Button className="mt-2" size="sm" variant="outline-primary" disabled={files.length === 0 || uploading} onClick={() => void upload()}>
        {uploading ? 'Uploading…' : 'Upload selected photos'}
      </Button>
      {error && (
        <Alert className="mt-2 mb-0 py-2" variant="warning">
          {error}
        </Alert>
      )}
    </div>
  )
}

export { AttachmentGallery, AttachmentUploader, PhotoInput }
