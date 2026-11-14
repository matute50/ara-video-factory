-- FASE 2: Tabla de cola de producción de videos Ara
-- Ejecutar en Supabase → SQL Editor
CREATE TABLE IF NOT EXISTS public.ara_video_jobs (
    id                  BIGSERIAL PRIMARY KEY,
    article_id          INTEGER NOT NULL UNIQUE REFERENCES public.articles(id) ON DELETE CASCADE,
    estado              TEXT NOT NULL DEFAULT 'pendiente_ensamblado',
    prompt_imagen       TEXT,
    prompt_veo          TEXT,
    imagen_compuesta_path TEXT,
    video_crudo_path    TEXT,
    video_final_path    TEXT,
    intentos            INTEGER NOT NULL DEFAULT 0,
    ultimo_error        TEXT,
    batch_report_json   JSONB,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
COMMENT ON TABLE public.ara_video_jobs IS 'Cola de producción de videos para el espacio Ara — Saladillo Vivo';
COMMENT ON COLUMN public.ara_video_jobs.estado IS 'pendiente_ensamblado | generando_imagen | generando_video | postproduccion | esperando_qc_telegram | aprobado | descartado | listo_entregado | error_reintentable | error_fatal';
CREATE INDEX IF NOT EXISTS idx_ara_video_jobs_estado ON public.ara_video_jobs(estado) WHERE estado IN ('pendiente_ensamblado','esperando_qc_telegram');
