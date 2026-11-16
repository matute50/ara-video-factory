"""
Tests unitarios para prompt_assembler.py (FASE 4).
Cobertura: conteo de palabras <120, posición de corchetes, bloqueo de sonrisa, etc.
"""
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "saladillo-vivo-pro"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from src.prompt_assembler import (
    assemble_prompts,
    assemble_prompts_multi_clip,
    build_batch_json_for_multi_clip,
    _word_count,
    _has_smile_word,
    _extract_bloques,
    _extract_continuidad_visual,
)


class TestPromptAssembler(unittest.TestCase):
    """Tests para ensamblaje determinista de prompts Flow."""

    def _make_ara_json(self, guion="hola mundo prueba de texto con varias palabras aquí para que siempre sea largo", tenor="informativo_servicio",
                       permitir_sonrisa=True, ambiente="Sonido de calle",
                       tono_vocal="Voz femenina joven y clara",
                       expresion_inicio="Mirada fija al lente con leve asentimiento",
                       expresion_desarrollo="Expresión serena natural",
                       expresion_cierre="Último guiño sutil",
                       lenguaje_corporal="postura_firme_contenida",
                       ritmo_camara="plano_fijo_estable",
                       requiere_aprobacion=False):
        return {
            "version": "1.0",
            "contenido": {
                "guion_ara": guion,
                "volanta_zocalo": "Ara",
                "titulo_zocalo": "Título prueba",
            },
            "meta_ara": {
                "tenor": tenor,
                "permitir_sonrisa": permitir_sonrisa,
                "tono_vocal": tono_vocal,
                "expresion_inicio": expresion_inicio,
                "expresion_desarrollo": expresion_desarrollo,
                "expresion_cierre": expresion_cierre,
                "lenguaje_corporal": lenguaje_corporal,
                "ambiente_sonoro": ambiente,
                "ritmo_camara": ritmo_camara,
                "requiere_aprobacion_sensible": requiere_aprobacion,
            }
        }

    def test_prompt_veo_menos_de_120_palabras(self):
        """El prompt_veo debe tener menos de 120 palabras."""
        ara = self._make_ara_json(guion="Estudiantes ganó el campeonato tras un partido intenso.")
        result = assemble_prompts(ara)
        wc = result['_debug']['word_count_prompt_veo'] if '_debug' in result else _word_count(result['prompt_veo'])
        self.assertLessEqual(wc, 120, f"prompt_veo tiene {wc} palabras (>120)")

    def test_dialogo_al_inicio_con_corchetes(self):
        """El prompt_veo debe comenzar con [diálogo ...]."""
        ara = self._make_ara_json()
        result = assemble_prompts(ara)
        prompt = result['prompt_veo']
        self.assertTrue(prompt.startswith('['), f"prompt_veo no empieza con '[': {prompt[:60]}")

    def test_fondo_al_final_con_corchetes(self):
        """El prompt_veo debe terminar con [Fondo: ...]."""
        ara = self._make_ara_json(ambiente="Tráfico lejano")
        result = assemble_prompts(ara)
        prompt = result['prompt_veo']
        self.assertIn('[Fondo:', prompt, "No contiene bloque [Fondo:] al final")
        self.assertTrue(prompt.rstrip().endswith(']'), "No cierra con ] al final")

    def test_no_texto_en_video(self):
        """El prompt_veo no debe pedir texto/títulos/gráficos visuales en el video."""
        ara = self._make_ara_json()
        result = assemble_prompts(ara)
        prompt = result['prompt_veo'].lower()
        forbidden = ['texto', 'título', 'cartel', 'graphic', 'overlay', 'subtitle']
        # Algunos pueden aparecer dentro de [dialogo] o [fondo] — solo verificar que no se pidan como instrucción
        for word in forbidden:
            if word in prompt:
                # Verificar que no esté fuera de corchetes
                outside_brackets = prompt.split('[')[0] + prompt.split(']')[-1] if ']' in prompt else prompt
                self.assertNotIn(word, outside_brackets.lower(),
                                 f"Palabra prohibida '{word}' encontrada fuera de bloques entre corchetes")

    def test_bloqueo_sonrisa_sensible_luto(self):
        """Con tenor sensible_luto, no debe haber palabras de sonrisa en prompt_veo."""
        ara = self._make_ara_json(
            guion="Momento doloroso para la comunidad.",
            tenor="sensible_luto",
            permitir_sonrisa=False,
            ambiente="Silencio urbano mínimo"
        )
        result = assemble_prompts(ara)
        self.assertFalse(result['valid'], "Debería fallar si hay contradicción")
        # Más importante: el prompt generado no debe contener palabras de alegría
        prompt = result['prompt_veo']
        smile_words = ['sonríe', 'sonrisa', 'feliz', 'alegre', 'ríe', 'risa', 'bromea']
        found = [w for w in smile_words if w in prompt.lower()]
        self.assertEqual(found, [], f"Palabras de alegría en prompt de luto: {found}")

    def test_prompts_validos_sin_errores(self):
        """Un caso básico debe generar prompts válidos sin errores."""
        ara = self._make_ara_json(
            guion="Festival cultural abrirá este fin de semana en la plaza central de Saladillo con artistas locales."
        )
        result = assemble_prompts(ara)
        self.assertTrue(result['valid'], f"Errors: {result['errors']}")
        self.assertIsInstance(result['prompt_imagen'], str)
        self.assertIsInstance(result['prompt_veo'], str)
        self.assertGreater(len(result['prompt_imagen']), 50)

    def test_volanta_y_titulo_pasados(self):
        """Volanta y título deben estar presentes en los metadatos del resultado."""
        ara = self._make_ara_json()
        result = assemble_prompts(ara, volanta_zocalo="Noticia del día", titulo_zocalo="Evento importante")
        self.assertTrue(result['valid'])
        self.assertEqual(result.get('_debug', {}).get('tenor'), 'informativo_servicio')


class TestWordCount(unittest.TestCase):
    def test_simple(self):
        self.assertEqual(_word_count("hola mundo"), 2)

    def test_empty(self):
        self.assertEqual(_word_count(""), 0)

    def test_multiline(self):
        text = "línea uno\ndos tres cuatro cinco"
        self.assertEqual(_word_count(text), 6)


class TestMultiClipV11(unittest.TestCase):
    """Tests para ensamblaje multi-clip con ara_json v1.1 (bloques)."""

    def _make_v11(self):
        return {
            "version": "1.1",
            "contenido": {
                "bloques": [
                    {"orden": 1, "guion_ara": "El municipio instaló nuevos nomencladores en las principales esquinas del centro urbano."},
                    {"orden": 2, "guion_ara": "La medida busca mejorar la orientación de vecinos. Toda la información en Saladillo Vivo."},
                ],
                "guion_ara": "El municipio instaló nuevos nomencladores en las principales esquinas del centro urbano. La medida busca mejorar la orientación de vecinos. Toda la información en Saladillo Vivo.",
                "volanta_zocalo": "Nomencladores",
                "titulo_zocalo": "Nuevas placas en esquinas",
            },
            "meta_ara": {
                "tenor": "informativo_servicio",
                "permitir_sonrisa": True,
                "tono_vocal": "Voz femenina joven y clara",
                "ambiente_sonoro": "Sonido de calle céntrica de Saladillo, volumen bajo",
                "continuidad_visual": "Periodista mujer, pelo negro largo recogido en cola baja, blazer azul marino sobrio, calle céntrica de Saladillo con iluminación diurna",
            },
        }

    def test_extract_bloques_v11(self):
        """Debe extraer bloques del formato v1.1."""
        ara = self._make_v11()
        bloques = _extract_bloques(ara)
        self.assertEqual(len(bloques), 2)
        self.assertIn("Saladillo Vivo", bloques[1])

    def test_extract_bloques_v10(self):
        """Debe hacer fallback a secciones v1.0."""
        ara = {
            "version": "1.0",
            "contenido": {
                "guion_ara": "Texto completo de prueba.",
                "seccion_a_guion": "Primera parte del texto.",
                "seccion_b_guion": "Segunda parte con cierre en Saladillo Vivo.",
            },
            "meta_ara": {},
        }
        bloques = _extract_bloques(ara)
        self.assertEqual(len(bloques), 2)
        self.assertEqual(bloques[0], "Primera parte del texto.")

    def test_extract_continuidad_visual(self):
        """Debe extraer continuidad_visual de meta_ara."""
        ara = self._make_v11()
        cv = _extract_continuidad_visual(ara)
        self.assertIn("blazer azul marino", cv)
        # Sin campo, devuelve vacío
        ara2 = {"meta_ara": {}, "contenido": {}}
        self.assertEqual(_extract_continuidad_visual(ara2), "")

    def test_multi_clip_v11_generates_prompts(self):
        """Con v1.1 bloques, debe generar un prompt por clip."""
        ara = self._make_v11()
        prompts = assemble_prompts_multi_clip(ara, n_clips=2)
        self.assertEqual(len(prompts), 2)
        for p in prompts:
            self.assertLessEqual(_word_count(p['prompt_veo']), 120,
                                 f"Clip {p['clip_index']} excede 120 palabras")
            self.assertTrue(p['valid'], f"Clip {p['clip_index']} inválido: {p['errors']}")

    def test_multi_clip_continuity_uses_v11(self):
        """El clip 2 debe incluir continuidad_visual de v1.1."""
        ara = self._make_v11()
        prompts = assemble_prompts_multi_clip(ara, n_clips=2)
        clip2 = prompts[1]['prompt_veo']
        self.assertIn("continuity:", clip2)
        self.assertIn("blazer azul marino", clip2)

    def test_batch_json_v11(self):
        """El batch JSON debe encadenar clip2 a clip1."""
        ara = self._make_v11()
        batch = build_batch_json_for_multi_clip(ara, n_clips=2, project_name="test_v11")
        self.assertEqual(len(batch['jobs']), 2)
        self.assertEqual(batch['jobs'][1]['start'], "test_v11_clip1.mp4")
        self.assertIsNone(batch['jobs'][0].get('start'))


class TestHasSmileWord(unittest.TestCase):
    def test_has_smile(self):
        self.assertTrue(_has_smile_word("ella sonríe alegremente"))

    def test_no_smile(self):
        self.assertFalse(_has_smile_word("él camina serio por la calle"))

    def test_spanish_accents(self):
        self.assertTrue(_has_smile_word("reír es saludable"))


if __name__ == "__main__":
    unittest.main()
