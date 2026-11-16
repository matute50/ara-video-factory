# Fase 2 — Generación de Imagen Maestra desde el Inspector — Diseño

## Intención y por qué
El botón "Regenerar imagen maestra" del tab Inspector (`/studio-fabrica/inspector`) es un `<Button>` muerto: no dispara nada y el panel muestra "Imagen maestra aún no generada". En Fase 1 la generación real quedó fuera de alcance. El objetivo de esta tarea es conectar ese botón con el **worker de Python existente** (`ara-video-factory/src/worker.py`) para que genere una imagen maestra real (modelo **Nano Banana 2 Lite** — el tier básico, no Pro) y la muestre en el Inspector.

## Enfoque
**Opción A — Cola Supabase con el worker existente.** El Inspector crea un job en `ara_video_jobs` con `tipo='imagen'` y `estado='pendiente_imagen'`. El worker (que ya hace *polling* sobre esa tabla) lo toma, ensambla solo el prompt de imagen, corre `flow.py batch` con un único job de imagen, sube el PNG al bucket `video-factory-assets/master-images/{job_id}.png` y actualiza `estado` + `master_image_url`. El Inspector hace *polling* del estado y muestra la imagen cuando queda lista.

## Metas
- El botón "Regenerar imagen maestra" crea un job de tipo imagen y muestra progreso.
- El worker genera la imagen (Nano Banana 2 Lite) y la sube a Storage.
- El Inspector muestra la imagen maestra generada y su estado.

## No-metas / anti-metas
- NO generación de video (se queda para una iteración posterior).
- NO cambiar el modelo de video ni el flujo de artículos automáticos existente.
- NO gastar créditos del modelo Pro (Se usa Nano Banana 2 Lite).
- NO romper la arquitectura del worker existente (se añade, no se reescribe).

## Restricciones
- Modelo de imagen: `Nano Banana 2 Lite` (default; override por `FLOW_IMAGE_MODEL`).
- Ratio: `9:16` (idéntico al resto del pipeline).
- Bucket Storage: `video-factory-assets`, carpeta `master-images/`.
- El worker usa `--live` para generar (consume créditos de Flow), `--dry-run` por defecto.

## Partes del cómo (decisiones tomadas)
1. **Migración SQL 003** en `ara_video_jobs`: columnas `tipo TEXT DEFAULT 'video'`, `master_image_url TEXT`.
2. **flow_bridge.py**: `IMAGE_MODEL = os.environ.get("FLOW_IMAGE_MODEL", "Nano Banana 2 Lite")`, usado en `defaults.image_model`.
3. **worker.py**: `process_image_only(storage, article_id)` — ensambla prompt, corre batch imagen, sube PNG a Storage, actualiza job. Helper `upload_to_bucket(...)`.
4. **worker.py main**: en `--poll`, además de los artículos, procesa jobs con `estado='pendiente_imagen'`.
5. **Panel Next.js**: rutas `/api/jobs` (GET lista / POST crear) y `/api/jobs/[id]` (GET estado), usando `createSupabaseAdminClient`.
6. **Inspector**: input ID + Buscar, botón "Regenerar imagen maestra" → POST job → poll → `<img>` con `master_image_url`.

## Queda para el ejecutor
- El prompt de imagen preview en el Inspector puede mostrar el prompt almacenado en el job (si existe) o un texto por defecto; no se reensambla en TS.