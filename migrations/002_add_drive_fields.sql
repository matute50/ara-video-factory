-- Migration 002: add Drive upload tracking fields to ara_video_jobs
ALTER TABLE public.ara_video_jobs
    ADD COLUMN IF NOT EXISTS drive_upload_ok BOOLEAN,
    ADD COLUMN IF NOT EXISTS drive_remote_url TEXT;

COMMENT ON COLUMN public.ara_video_jobs.drive_upload_ok IS 'True si el video se subió exitosamente a Google Drive';
COMMENT ON COLUMN public.ara_video_jobs.drive_remote_url IS 'URL del archivo en Google Drive';
