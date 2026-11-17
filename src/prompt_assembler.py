"""
Ensamblador determinista de prompts para Google Flow (Veo 3.1).
Lee ara_json de un artículo y construye prompt_imagen + prompt_veo
cumpliendo restricciones de longitud (<120 palabras) y formato.
"""
from __future__ import annotations
import json, os, re
from pathlib import Path
from typing import Dict, Optional

# ---------------------------------------------------------------------------
# Configuración
# ---------------------------------------------------------------------------
# El .env vive en la raíz del reposorio, no en src/
ENV_PATH = Path(__file__).resolve().parent.parent / ".env"

def _read_env(path: Path = ENV_PATH) -> dict:
    env: Dict[str, str] = {}
    if path.exists():
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    env[k.strip()] = v.strip().strip("\"'")
    return env

_CFG = _read_env()
FLOW_USE_CHARACTER: bool = _CFG.get("FLOW_USE_CHARACTER", "false").lower() == "true"
FLOW_BASE_IMAGE_REF: str = _CFG.get("FLOW_BASE_IMAGE_REF", "")
# Ruta relativa desde repo al folder de fotos reales de Saladillo centro
SALADILLO_PHOTO_DIR: str = _CFG.get("SALADILLO_PHOTO_DIR", "./reference_photos/center_saladillo/")


def _has_smile_word(text: str) -> bool:
    """Detecta palabras de sonrisa/entusiasmo prohibidas en tenores graves."""
    import unicodedata as _u
    SMILES_RAW = {"sonríe", "sonrisa", "feliz", "alegre", "risa", "ríe", "reír", "reí", "bromea"}
    # Normalizar tanto el texto como el set de palabras para comparar sin acentos
    normalized_text = _u.normalize("NFD", text.lower())
    stripped_text = ''.join(c for c in normalized_text if not _u.category(c).startswith('M'))
    words = set(re.findall(r'\b[a-z]+\b', stripped_text))
    stripped_smiles = {
        ''.join(c for c in _u.normalize("NFD", s) if not _u.category(c).startswith('M'))
        for s in SMILES_RAW
    }
    return bool(words & stripped_smiles)


def _word_count(text: str) -> int:
    return len(text.split())


def _extract_guion(ara_json: dict) -> str:
    """Extrae guion_ara del JSON Ara con fallback seguro."""
    try:
        if isinstance(ara_json, str):
            parsed = json.loads(ara_json)
        else:
            parsed = ara_json
        return parsed.get("contenido", {}).get("guion_ara", "")
    except (json.JSONDecodeError, AttributeError, KeyError):
        return ""


def _extract_meta(ara_json: dict) -> dict:
    """Extrae meta_ara del JSON Ara con fallback seguro."""
    try:
        if isinstance(ara_json, str):
            parsed = json.loads(ara_json)
        else:
            parsed = ara_json
        return parsed.get("meta_ara", {})
    except (json.JSONDecodeError, AttributeError, KeyError):
        return {}


def assemble_prompts(
    ara_json_input,
    volanta_zocalo: str = "",
    titulo_zocalo: str = "",
    allow_smile: Optional[bool] = None,
) -> Dict[str, str]:
    """
    Construye los prompts para Flow respetando todas las reglas.

    Parámetros:
        ara_json_input:      dict o JSON-string con estructura Ara
        volanta_zocalo:      texto para la banda superior del zócalo
        titulo_zocalo:       título principal del zócalo
        allow_smile:         override manual; None = usa permiso del tenor

    Retorna:
        {
          'prompt_imagen': str,
          'prompt_veo': str,
          'valid': bool,
          'errors': list[str],
        }
    """
    errors: list[str] = []
    guion = _extract_guion(ara_json_input)
    meta = _extract_meta(ara_json_input)
    tenor = meta.get("tenor", "informativo_servicio")
    persona_explicitly_male = False
    tono_vocal_raw = (meta.get("tono_vocal") or "").lower()
    if any(tok in tono_vocal_raw for tok in ["varonil", "masculino", "hombre", "barítono"]):
        persona_explicitly_male = True
    permit_smile = (
        allow_smile if allow_smile is not None
        else meta.get("permitir_sonrisa", tenor not in ("sensible_luto", "policial_grave"))
    )
    ambiente_sonoro = meta.get("ambiente_sonoro", "Silencio urbano mínimo, sin música ni ruido destacado")
    expresion_inicio = meta.get("expresion_inicio", "Mirada fija al lente, leve asentimiento inicial, expresión neutra-contenida")
    expresion_desarrollo = meta.get("expresion_desarrollo", "Expresión serena, asentimientos naturales, gestos leves de manos")
    expresion_cierre = meta.get("expresion_cierre", "Último guiño sutil hacia cámara, pausa breve antes de bajar el tono")
    lenguaje_corporal = meta.get("lenguaje_corporal", "postura_firme_contenida")
    ritmo_camara = meta.get("ritmo_camara", "plano_fijo_estable")

    # ---- Validaciones ----
    if not guion:
        errors.append("'guion_ara' está vacío")
    if not isinstance(guion, str):
        errors.append("'guion_ara' debe ser string")
        guion = ""

    if persona_explicitly_male:
        errors.append("'tono_vocal' contiene término masculino — se requiere voz femenina")

    if tenor in ("sensible_luto", "policial_grave"):
        if _has_smile_word(guion):
            errors.append("El tenor es sensible/grave pero el guion contiene palabras de alegría/sonrisa")
        if not permit_smile:
            errors.append("El tenor prohíbe expresiones sonrientes")

    if _word_count(guion) < 14:
        errors.append(f"'guion_ara' muy corto ({_word_count(guion)} palabras, mínimo 14)")
    elif _word_count(guion) > 45:
        errors.append(f"'guion_ara' muy largo ({_word_count(guion)} palabras, máximo 45)")

    # ---- Prompt IMAGEN (9:16 vertical) ----
    persona_marker = "@Ara" if FLOW_USE_CHARACTER and FLOW_BASE_IMAGE_REF else "periodista femenino profesional"
    tenor_lighting_map = {
        "sensible_luto": "Iluminación natural tenue, sombras suaves, atmósfera sobria",
        "policial_grave": "Iluminación natural diurna moderada, realismo periodístico",
        "urgencias_policiales": "Iluminación mixta día/noche según hora del hecho, realismo policial",
        "alegre_cultural": "Iluminación natural brillante, tonos cálidos diurnos",
        "informativo_servicio": "Iluminación natural diurna equilibrada, estilo noticiero",
    }
    lighting = tenor_lighting_map.get(tenor, "Iluminación natural diurna equilibrada")
    vestimenta_note = (
        "vestimenta sobria y discreta (colores oscuros/neutros)"
        if tenor in ("sensible_luto", "policial_grave")
        else "vestimenta profesional sobria"
    )
    logging_cfg = _read_env()
    flujo_img = logging_cfg.get("SALADILLO_PHOTO_DIR", "./reference_photos/center_saladillo/")
    # Extraer descripción fija de continuidad visual (look completo del personaje)
    raw_cv = ""
    try:
        parsed_ara = ara_json_input if isinstance(ara_json_input, dict) else json.loads(ara_json_input)
        raw_cv = (parsed_ara.get("meta_ara") or {}).get("continuidad_visual", "") or ""
    except Exception:
        pass
    # Si no hay descripción fija (v1.0 o agente antiguo), agregar nota genérica
    if not raw_cv:
        raw_cv = "Mujer joven con pelo marrón oscuro, largo, con ondas naturales, suelto o con accesorios discretos (diadema fina o clip), vestimenta casual urbana (jeans, campera liviana o remera lisa), maquillaje natural sutil, mirada cercana y serena, piel clara, iluminación natural diurna frontal suave"
    imagen_refs = (
        f"Referencia visual: foto de {persona_marker} combinada con fotos reales del centro de Saladillo ({SALADILLO_PHOTO_DIR}). "
        if FLOW_USE_CHARACTER and FLOW_BASE_IMAGE_REF
        else ""
    )
    prompt_imagen = (
        f"Vertical 9:16. Close-up medio de {persona_marker}, tercio superior-medio, mirando directamente a cámara. "
        f"{lighting}. {vestimenta_note}. "
        f"{imagen_refs}"
        f"Look exacto: {raw_cv}. "
        f"Ambiente urbano exterior, escena real callejera de ciudad argentina mediana, "
        f"luz natural, fotografía periodística. NO estudio, NO fondo neutro, "
        f"sin carteles ni letreros con texto visible en la escena."
    )

    # ---- Prompt VIDEO (Veo 3.1, <120 palabras) ----
    dialogo_bracket = (
        f'[El personaje dice "{guion}", {tono_vocal_raw or "voz femenina clara y profesional"}, sincronización labial precisa]'
        if not FLOW_USE_CHARACTER
        else f'[@Ara dice "{guion}", {tono_vocal_raw or "voz femenina clara y profesional"}, sincronización labial precisa]'
    )
    visual_direction = (
        f"Expressive style: initial={expresion_inicio}; development={expresion_desarrollo}; closing={expresion_cierre}; "
        f"body_language={lenguaje_corporal}; camera_rhythm={ritmo_camara}"
    )
    # Restricción explícita: sin estudio, sin carteles con texto en la escena generada
    scene_constraint = "Scene is an outdoor real street setting, NEVER a studio set; NO visible text signs, posters, or graphics."
    visual_direction += f"; {scene_constraint}"
    audio_block = f"[Fondo: {ambiente_sonoro}]"
    prompt_veo = f"{dialogo_bracket}; {visual_direction}; {audio_block}"

    # Verificación crítica: menos de 120 palabras
    wc = _word_count(prompt_veo)
    if wc > 120:
        # Truncar la dirección visual manteniendo diálogo + fondo
        available = 120 - _word_count(dialogo_bracket) - _word_count(audio_block) - 2
        parts = visual_direction.split("; ")
        truncated_parts = []
        count = 0
        for part in parts:
            pc = _word_count(part)
            if count + pc + 1 > available:
                break
            truncated_parts.append(part)
            count += pc + 1
        condensed_visual = "; ".join(truncated_parts)
        prompt_veo = f"{dialogo_bracket}; {condensed_visual}; {audio_block}"
        wc = _word_count(prompt_veo)

    if wc > 120:
        errors.append(f"prompt_veo excede 120 palabras después de truncado ({wc})")

    # Prohibir texto/carteles en video — verificar que ninguna palabra prohibida
    # aparezca fuera de los corchetes del diálogo o del bloque [Fondo:]
    forbidden_video_text = {
        "texto", "título", "cartel", "gráfico", "letra", "subtitle", "overlay",
        "banner", "titular", "lead-in", "on-screen", "text overlay",
    }
    # Extraer solo el texto fuera de corchetes para chequeo
    outside = re.sub(r'\[[^\]]*\]', ' ', prompt_veo)
    for word in forbidden_video_text:
        if word in outside.lower():
            errors.append(f"Palabra '{word}' detectada fuera de bloques entre corchetes en prompt_veo")

    valid = len(errors) == 0
    return {
        "prompt_imagen": prompt_imagen,
        "prompt_veo": prompt_veo,
        "valid": valid,
        "errors": errors,
        "_debug": {
            "word_count_prompt_veo": wc,
            "tenor": tenor,
            "persona_explicitly_male": persona_explicitly_male,
            "allow_smile": permit_smile,
            "dialogo_bracket_length": len(dialogo_bracket),
        },
    }


def dialogo_or_fondo_safe(word: str) -> bool:
    """Palabras que pueden aparecer legítimamente dentro de corchetes [dialogo] o [Fondo: ...]."""
    safe_in_brackets = {"texto", "titulo", "grafico", "letra", "subtitulo"}
    # Solo permitir si está DENTRO de un paréntesis cuadrado en el prompt
    return False  # Mantener estricto: ninguna de estas va en el prompt general


def _extract_bloques(ara_json_input) -> list[str]:
    """Extrae la lista de guiones por bloque de un ara_json v1.1, o fallback a v1.0."""
    try:
        if isinstance(ara_json_input, str):
            parsed = json.loads(ara_json_input)
        else:
            parsed = ara_json_input
        contenido = parsed.get("contenido", {})
        # v1.1: bloques es una lista de dicts con 'guion_ara'
        bloques = contenido.get("bloques", [])
        if isinstance(bloques, list) and len(bloques) >= 2:
            return [b["guion_ara"] for b in bloques if isinstance(b, dict) and "guion_ara" in b]
        # v1.0: secciones individuales
        sec_a = contenido.get("seccion_a_guion", "")
        sec_b = contenido.get("seccion_b_guion", "")
        if sec_a and sec_b:
            return [sec_a, sec_b]
        # Fallback al guion completo
        guion = _extract_guion(ara_json_input)
        if guion:
            return [guion]
    except Exception:
        pass
    return []


def _extract_continuidad_visual(ara_json_input) -> str:
    """Extrae continuidad_visual de meta_ara (v1.1) o cadena vacía."""
    try:
        if isinstance(ara_json_input, str):
            parsed = json.loads(ara_json_input)
        else:
            parsed = ara_json_input
        return parsed.get("meta_ara", {}).get("continuidad_visual", "") or ""
    except Exception:
        return ""


def assemble_prompts_multi_clip(
    ara_json_input,
    n_clips: int = 2,
    duracion_por_clip: float = 8.0,
) -> list[dict]:
    """
    Construye prompts para múltiples clips de video a partir de un solo ara_json.

    Cada clip usa su propia sección del guión (seccion_a_guion / seccion_b_guion).
    El segundo clip reutiliza la referencia visual del primero (continuidad).

    Retorna:
        lista de dicts {
          'prompt_imagen': str,
          'prompt_veo': str,
          'duracion_sec': float,
          'clip_index': int,
          'valid': bool,
          'errors': list[str],
        }
    """
    results = []
    meta = _extract_meta(ara_json_input)
    tenor = meta.get("tenor", "informativo_servicio")
    permiso_sonrisa = meta.get("permitir_sonrisa", tenor not in ("sensible_luto", "policial_grave"))
    tono_vocal_raw = (meta.get("tono_vocal") or "").lower()
    if any(tok in tono_vocal_raw for tok in ["varonil", "masculino", "hombre", "barítono"]):
        pass  # warning en logs pero no bloqueamos
    ambiente_sonoro = meta.get("ambiente_sonoro", "Silencio urbano mínimo, sin música ni ruido destacado")
    expresion_inicio = meta.get("expresion_inicio", "Mirada fija al lente, leve asentimiento inicial, expresión neutra-contenida")
    expresion_desarrollo = meta.get("expresion_desarrollo", "Expresión serena, asentimientos naturales, gestos leves de manos")
    expresion_cierre = meta.get("expresion_cierre", "Despedida con leve asentimiento sereno, mantiene la mirada al lente antes de cerrar")
    lenguaje_corporal = meta.get("lenguaje_corporal", "postura_firme_contenida")
    ritmo_camara = meta.get("ritmo_camara", "plano_fijo_estable")

    persona_marker = "@Ara" if FLOW_USE_CHARACTER and FLOW_BASE_IMAGE_REF else "periodista femenino profesional"
    tenor_lighting_map = {
        "sensible_luto": "Iluminación natural tenue, sombras suaves, atmósfera sobria",
        "policial_grave": "Iluminación natural diurna moderada, realismo periodístico",
        "urgencias_policiales": "Iluminación mixta día/noche según hora del hecho, realismo policial",
        "alegre_cultural": "Iluminación natural brillante, tonos cálidos diurnos",
        "informativo_servicio": "Iluminación natural diurna equilibrada, estilo noticiero",
    }
    lighting = tenor_lighting_map.get(tenor, "Iluminación natural diurna equilibrada")
    vestimenta_note = (
        "vestimenta sobria y discreta (colores oscuros/neutros)"
        if tenor in ("sensible_luto", "policial_grave")
        else "vestimenta profesional sobria"
    )
    imagen_refs = (
        f"Referencia visual: foto de {persona_marker} combinada con fotos reales del centro de Saladillo ({SALADILLO_PHOTO_DIR}). "
        if FLOW_USE_CHARACTER and FLOW_BASE_IMAGE_REF
        else ""
    )
    # Nota genérica de look cuando no hay continuidad explícita en meta_ara (v1.0)
    default_cv = ""
    base_imagen_prompt = (
        f"Vertical 9:16. Close-up medio de {persona_marker}, tercio superior-medio, mirando directamente a cámara. "
        f"{lighting}. {vestimenta_note}. "
        f"{imagen_refs}"
        f"Ambiente urbano exterior, escena real callejera de ciudad argentina mediana, "
        f"luz natural, fotografía periodística. NO estudio, NO fondo neutro, "
        f"sin carteles ni letreros con texto visible en la escena."
    )

    # Determinar qué secciones usar (prioridad: v1.1 bloques > v1.0 secciones > guion completo)
    sections = _extract_bloques(ara_json_input)
    guion_completo = _extract_guion(ara_json_input)
    continuidad = _extract_continuidad_visual(ara_json_input)

    # Inyectar look completo en el prompt de imagen para CLIP 1 también
    if not continuidad:
        default_cv = "Mujer joven con pelo marrón oscuro, largo, con ondas naturales, suelto o con accesorios discretos (diadema fina o clip), vestimenta casual urbana (jeans, campera liviana o remera lisa), maquillaje natural sutil, mirada cercana y serena, piel clara, iluminación natural diurna frontal suave"
    else:
        default_cv = continuidad
    base_imagen_prompt = (
        f"Vertical 9:16. Close-up medio de {persona_marker}, tercio superior-medio, mirando directamente a cámara. "
        f"{lighting}. {vestimenta_note}. "
        f"{imagen_refs}"
        f"Look exacto: {default_cv}. "
        f"Ambiente urbano exterior, escena real callejera de ciudad argentina mediana, "
        f"luz natural, fotografía periodística. NO estudio, NO fondo neutro, "
        f"sin carteles ni letreros con texto visible en la escena."
    )

    if not sections and guion_completo:
        # Fallback: dividir guion completo en n_clips mitades
        all_words = guion_completo.split()
        per_clip = max(14, len(all_words) // n_clips)
        for i in range(n_clips):
            start = i * per_clip
            end = min(start + per_clip, len(all_words))
            s = ' '.join(all_words[start:end]).rstrip()
            if not s.endswith('.') and end < len(all_words):
                # Buscar frontera de oración cercana
                for j in range(end, min(end + 5, len(all_words))):
                    cand = ' '.join(all_words[start:j+1]).rstrip()
                    if cand.endswith('.'):
                        s = cand
                        end = j + 1
                        break
                else:
                    s += '.'
            elif not s.endswith('.'):
                s += '.'
            sections.append(s)

    for idx, seccion_text in enumerate(sections[:n_clips]):
        errors = []
        if not seccion_text:
            errors.append(f"'guion_ara' está vacío en clip {idx+1}")
        if _word_count(seccion_text) < 8:
            errors.append(f"Clip {idx+1}: sección muy corta ({_word_count(seccion_text)} palabras)")
        elif _word_count(seccion_text) > 25:
            errors.append(f"Clip {idx+1}: sección muy larga ({_word_count(seccion_text)} palabras, máx 25)")

        # Prompt imagen: idéntico para todos los clips (mismo personaje/escenario)
        prompt_imagen = base_imagen_prompt

        # Prompt video
        cierre_desc = expresion_cierre if idx == len(sections) - 1 else expresion_desarrollo
        voz_fallback = tono_vocal_raw or "voz femenina clara y profesional"
        if not FLOW_USE_CHARACTER:
            dialogo_bracket = (
                f'[El personaje dice "{seccion_text}", {voz_fallback}, '
                'sincronización labial precisa]'
            )
        else:
            dialogo_bracket = (
                f'[@Ara dice "{seccion_text}", {voz_fallback}, '
                'sincronización labial precisa]'
            )
        visual_direction = (
            f"Expressive style: initial={expresion_inicio}; development={expresion_desarrollo}; closing={cierre_desc}; "
            f"body_language={lenguaje_corporal}; camera_rhythm={ritmo_camara}"
        )
        # Restricción explícita: sin estudio, sin carteles con texto en la escena generada
        scene_constraint = "Scene is an outdoor real street setting, NEVER a studio set; NO visible text signs, posters, or graphics."
        visual_direction += f"; {scene_constraint}"

        # Nota de continuidad para clips posteriores: forzar identidad visual
        continuity_directive = ""
        if idx > 0:
            if continuidad:
                # v1.1: usar la descripción de continuidad fija del meta_ara
                continuity = (
                    "Keep EXACTLY the visual continuity described: "
                    f"{continuidad}. "
                    "Only change lip movement and facial expression to match the new dialogue."
                )
            else:
                continuity = (
                    "Keep EXACTLY the same character face, outfit, hair, skin tone, "
                    "and background scene from the previous clip — only change lip movement "
                    "and facial expression to match the new dialogue."
                )
            continuity_directive = f"; continuity:{continuity}"

        audio_block = f"[Fondo: {ambiente_sonoro}]"
        prompt_veo = f"{dialogo_bracket}; {visual_direction}{continuity_directive}; {audio_block}"

        # Verificación crítica: menos de 120 palabras
        wc = _word_count(prompt_veo)
        if wc > 120:
            # Preservar la directiva de continuidad (crítica para coherencia visual)
            available = 120 - _word_count(dialogo_bracket) - _word_count(audio_block) - _word_count(continuity_directive) - 2
            parts = visual_direction.split("; ")
            truncated_parts = []
            count = 0
            for part in parts:
                pc = _word_count(part)
                if count + pc + 1 > available:
                    break
                truncated_parts.append(part)
                count += pc + 1
            condensed_visual = "; ".join(truncated_parts)
            prompt_veo = f"{dialogo_bracket}; {condensed_visual}{continuity_directive}; {audio_block}"
            wc = _word_count(prompt_veo)

        valid = len(errors) == 0
        results.append({
            "prompt_imagen": prompt_imagen,
            "prompt_veo": prompt_veo,
            "duracion_sec": duracion_por_clip,
            "clip_index": idx + 1,
            "valid": valid,
            "errors": errors,
        })

    return results


def build_batch_json_for_multi_clip(
    ara_json_input,
    n_clips: int = 2,
    duracion_por_clip: float = 8.0,
    project_name: str = "",
) -> dict:
    """
    Construye el JSON de batch para flow.py con encadenamiento de continuidad.

    Cada clip posterior apunta al output del anterior mediante 'start':
      job_2.start = job_1.name + '.mp4'

    Retorna un dict compatible con la estructura que espera flow.py batch:
    {
      "project": "<name>",
      "defaults": {"ratio": "9:16", "video_model": "Veo 3.1 - Lite"},
      "jobs": [
        {"type":"video","name":"<id>_clip1","prompt":"...","image":null},
        {"type":"video","name":"<id>_clip2","prompt":"...","start":"<id>_clip1.mp4"},
      ]
    }
    """
    import time
    prompts = assemble_prompts_multi_clip(
        ara_json_input, n_clips=n_clips, duracion_por_clip=duracion_por_clip
    )
    if not prompts:
        raise ValueError("No se generaron prompts para ningún clip")

    # Extraer id único del ara_json (si existe) o timestamp
    raw_id = None
    try:
        parsed = ara_json_input if isinstance(ara_json_input, dict) else json.loads(ara_json_input)
        c = parsed.get("contenido", {})
        raw_id = c.get("article_id") or c.get("id")
    except Exception:
        pass
    if not raw_id:
        raw_id = f"batch_{int(time.time())}"

    safe_id = re.sub(r"[^A-Za-z0-9_-]", "_", str(raw_id))
    prefix = project_name or safe_id

    jobs = []
    prev_name = None
    for p in prompts:
        job = {
            "type": "video",
            "name": f"{prefix}_clip{p['clip_index']}",
            "prompt": p["prompt_veo"],
        }
        if prev_name:
            # El segundo clip y siguientes apuntan al video anterior como frame inicial
            job["start"] = prev_name + ".mp4"
        # Note: 'image' field is only consumed by image-type jobs; video jobs ignore it.
        # Visual identity is enforced via the prompt and continuity directive above.
        jobs.append(job)
        prev_name = job["name"]

    return {
        "project": prefix,
        "defaults": {"ratio": "9:16", "video_model": "Veo 3.1 - Lite"},
        "jobs": jobs,
    }


    import argparse
    parser = argparse.ArgumentParser(description="Assemble Flow prompts from ara_json")
    parser.add_argument("--input", required=True, help="Path to ara_json file")
    parser.add_argument("--volanta", default="", help="Volanta text")
    parser.add_argument("--titulo", default="", help="Titulo text")
    args = parser.parse_args()
    with open(args.input, encoding="utf-8") as f:
        data = json.load(f)
    out = assemble_prompts(data, args.volanta, args.titulo)
    print(json.dumps(out, indent=2, ensure_ascii=False))
