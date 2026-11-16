-- Migración 003 — Fase 2: Imagen maestra desde Inspector
-- Agrega a ara_video_jobs las columnas `tipo` y `master_image_url`.
ALTER TABLE public.ara_video_jobs
  ADD COLUMN IF NOT EXISTS tipo TEXT NOT NULL DEFAULT 'video',
  ADD COLUMN IF NOT EXISTS master_image_url TEXT;

COMMENT ON COLUMN public.ara_video_jobs.tipo IS 'imagen | video';
COMMENT ON COLUMN public.ara_video_jobs.master_image_url IS 'URL pública en Supabase Storage de la imagen maestra';