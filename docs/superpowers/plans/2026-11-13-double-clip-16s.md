# Double Clip 16s Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Generar videos de noticias Ara de 16 segundos usando dos clips de 8s concatenados, con prompt Ara que divide el guión en secciones A y B.

**Architecture:** 
1. `AraScriptAgent` genera `seccion_a_guion` (~17 palabras) + `seccion_b_guion` (~18 palabras), separado por palabra de corte.
2. `prompt_assembler.py` produce dos sets `(imagen, veo)` desde un solo ara_json, reusando la imagen del primer clip como referencia para el segundo.
3. `ffmpeg_composer.py` concatena ambos clips y aplica zócalo azul único con marquee coordinado a la duración total.

**Tech Stack:** Python 3.14, FFmpeg, Google Flow (Veo 3.1 Lite), Supabase

**Spec:** Videos mínimos 16s = 2 clips de 8s cada uno. Guion Ara debe tener ~35 palabras divididas equitativamente. Un solo zócalo azul (#1E3A8A) con texto ARA dorado fijo y scroll único en título.

## Global Constraints

- Cada sección del guión termina en oración completa (prohibido cortar palabras).
- La última sección siempre cierra mencionando "Saladillo Vivo".
- `corte_en_palabra`: palabra exacta donde termina la sección A; es el índice split+1 de la primera sección.
- Prompt Veo máx 120 palabras por clip.
- Imagen base reutilizada: Clip 1 genera nueva imagen, Clip 2 usa `--start` con resultado de Clip 1.
- Zócalo: fondo azul #1E3A8A, etiqueta ARA dorada fija arriba-izq., volanta estática, título en scroll de línea única coordinado con duración total.

## Review Focus

1. Corte limpio entre secciones: la sección A termina en punto seguido, sin truncar oraciones.
2. Reuse de imagen: el segundo prompt `--start` apunta al frame del primer clip exitosamente.
3. Concatenación sin gap: los clips unidos tienen transición transparente o corte directo aceptable.
4. Duración final >= 16s: verificación post-concatenación con ffprobe.
5. Audio sincronizado: el audio completo (>16s incluyendo promo) se mezcla sobre la secuencia concatenada.

---

## Task 1: AraScriptAgent — Split Script Schema

**Files:**
- Modify: `/home/matias/saladillo-vivo-pro/agents/ara_script_agent.py` (líneas ~223-314, ~641-728, ~476-590)

**Interfaces:**
- Consumes: template `_build_prompt()` actual
- Produces: nuevo campo `seccion_a_guion` y `seccion_b_guion` en JSON de salida

- [ ] **Step 1: Modificar _build_prompt para solicitar guión dividido**

En `_build_prompt()`, añadir instrucciones al prompt TEMPLATE que soliciten un JSON con:
```json
{
  "contenido": {
    "guion_ara": "__(combinado original)__: ",
    "seccion_a_guion": "__ejemplo: Oraciones iniciales que presentan la noticia.__",
    "seccion_b_guion": "__ejemplo: Oraciones finales que incluyen invitación a Saladillo Vivo.__",
    "corte_en_palabra": "__última palabra de sección A__"
  }
}
```
Reglas nuevas:
- `seccion_a_guion`: 15-18 palabras, introduce el tema, datos principales. Termina con oración completa.
- `seccion_b_guion`: 15-18 palabras, continuation natural desde corte, termina con invitación a Saladillo Vivo.
- `corte_en_palabra`: última palabra de `seccion_a_guion` (sin puntuación).
- `guion_ara` mantiene el texto completo combinado para compatibilidad.

- [ ] **Step 2: Actualizar validate_ara_json para soportar campos nuevos**

Añadir validación para `seccion_a_guion` (14-18 palabras), `seccion_b_guion` (14-18 palabras), y `corte_en_palabra` (presente y no vacío). Aceptar si estos campos existen en el JSON; si no existen (JSON legacy), usar valores vacíos para compatibilidad con artículos existentes.

- [ ] **Step 3: Actualizar apply_heuristic_fallback para generar secciones**

Si `seccion_a_guion` no está presente, dividir el guion heurísticamente en dos mitades aproximadamente iguales, dividiendo en el último "." antes de la mitad del texto. Calcular `corte_en_palabra` como última palabra de la sección A.

- [ ] **Step 4: Verificar tests actuales siguen pasando**
Run: `cd /home/matias/saladillo-vivo-pro && python3 -m pytest tests/test_ara_script_agent.py -v`
Expected: todos los tests existentes pasan (con warnings no fatal si campos nuevos ausentes).

- [ ] **Step 5: Commit**
```bash
git add agents/ara_script_agent.py tests/test_ara_script_agent.py
git commit -m "feat(ara): split script schema for double-clip 16s videos"
```

---

## Task 2: PromptAssembler — Multi-Clip Support

**Files:**
- Modify: `/home/matias/ara-video-factory/src/prompt_assembler.py`

**Interfaces:**
- Consumes: `assemble_prompts()` existente (mantener intacto para compatibilidad)
- Produces: `assemble_prompts_multi_clip(ara_json, n_clips=2) -> list[dict]`

- [ ] **Step 1: Escribir test fallido para multi_clip**
```python
def test_multi_clip_produces_two_sets():
    ara = {"contenido": {"guion_ara": "El papa León XIV presidió este jueves una vigilia con cerca de 60.000 jóvenes en el Estadio Nacional. Toda la información en Saladillo Vivo.", "volanta_zocalo": "León XIV encabezó", "titulo_zocalo": "El papa León XIV presidió este jueves una"}, "meta_ara": {...}}
    clips = assemble_prompts_multi_clip(ara, n_clips=2)
    assert len(clips) == 2
    assert "prompt_imagen" in clips[0]
    assert "prompt_veo" in clips[0]
    assert "prompt_imagen" in clips[1]
    assert "prompt_veo" in clips[1]
```

- [ ] **Step 2: Implementar assemble_prompts_multi_clip**

La función debe:
1. Extraer `seccion_a_guion` y `seccion_b_guion` del JSON (fallback a dividir `guion_ara` en 2 partes si no están presentes).
2. Para el CLIP 1: construir `prompt_imagen` normal, `prompt_veo` con solo la sección A.
3. Para el CLIP 2: construir `prompt_imagen` idéntico, `prompt_veo` con solo la sección B. Marcar en prompt_veo del clip 2 que es continuación.
4. Retornar lista de dicts `{"prompt_imagen": str, "prompt_veo": str, "duracion_sec": 8.0}`.

Nota: el prompt Veo del clip 2 debe incluir indicación visual de continuidad (ej: "continúa el personaje hablando...").

- [ ] **Step 3: Ejecutar tests**
Run: `cd /home/matias/ara-video-factory && python3 -m pytest tests/ -v -k "multi_clip or prompt" 2>&1 | tail -30`
Expected: tests passing.

- [ ] **Step 4: Commit**
```bash
git add src/prompt_assembler.py tests/
git commit -m "feat(prompts): multi-clip prompt assembly for 16s double video"
```

---

## Task 3: FfmpegComposer — Video Concatenation + Extended Zócalo

**Files:**
- Modify: `/home/matias/ara-video-factory/src/ffmpeg_composer.py`

**Interfaces:**
- Consumes: `process_video()` existente (mantener), `draw_zocalo()` existente
- Produces: `concatenate_videos(paths, output_path) -> bool`, nuevo `process_video_multi_clip(video_paths, output_path, cfg, audio_url=None)` 

- [ ] **Step 1: Agregar función concatenate_videos**

```python
def concatenate_videos(input_paths: list[str], output_path: str, target_duration_min: float = 16.0) -> bool:
    """Concatena varios clips MP4 vertical 1080x1920 en uno solo. Escala cada clip a 1080x1920 antes de unir."""
    # Crear filelist.txt para ffmpeg concat demuxer
    tmp_dir = tempfile.mkdtemp(prefix="concat_")
    try:
        listfile = os.path.join(tmp_dir, "files.txt")
        with open(listfile, "w") as f:
            for p in input_paths:
                f.write(f"file '{p}'\n")
        
        args = [
            "-f", "concat", "-safe", "0", "-i", listfile,
            "-c:v", "libx264", "-preset", "medium", "-crf", "20",
            "-c:a", "aac", "-b:a", "128k",
            "-movflags", "+faststart",
            output_path,
        ]
        rc, _, err = run_ffmpeg(args)
        return rc == 0
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
```

- [ ] **Step 2: Crear process_video_multi_clip**

Nueva función que:
1. Escala cada clip individualmente a 1080x1920 (reutiliza `ensure_1080x1920`).
2. Aplica draw_zocalo a CADA clip individualmente con duration_sec=8.0.
3. Concatena los clips zocalizados con `concatenate_videos`.
4. Si se proporciona `audio_url`, descarga el audio y lo mezcla con `-shortest`.
5. Retorna dict con estado de cada paso.

- [ ] **Step 3: Ejecutar pruebas básicas**
Run: `cd /home/matias/ara-video-factory && python3 -m pytest tests/ -v 2>&1 | tail -20`
Expected: existing tests still pass.

- [ ] **Step 4: Commit**
```bash
git add src/ffmpeg_composer.py
git commit -m "feat(composer): add concatenate_videos and process_video_multi_clip for 16s output"
```

---

## Task 4: End-to-End Test Article 901

**Files:**
- Execute directly via terminal commands

**Interfaces:**
- Consumes: todas las modificaciones anteriores
- Produces: video final de 16s para artículo 901 subido a Drive

- [ ] **Step 1: Regenerar ara_json del artículo 901 con secciones splitting**
Run: El agente que regenere el JSON usando el nuevo modelo Ara y guarde en Supabase.

- [ ] **Step 2: Generar clips con Flow**
- Generar imagen base (Clip 1) con `flow.py image`.
- Generar Video Clip 1 (8s) con `flow.py video --prompt section_A --start imagen.png`.
- Generar Video Clip 2 (8s) con `flow.py video --prompt section_B --start clip1_frame_final.png` (o reutilizar imagen base).

- [ ] **Step 3: Componer video final**
Usar `process_video_multi_clip([clip1_path, clip2_path], output, cfg, audio_url=...)` y verificar duración final con ffprobe (debe ser >= 16s).

- [ ] **Step 4: Subir a Drive**
Subir el video final y limpiar versiones antiguas.

- [ ] **Step 5: Commit**
```bash
git add .
git commit -m "test(901): end-to-end double-clip 16s video generation with new Ara split schema"
```
