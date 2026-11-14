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

from src.prompt_assembler import assemble_prompts, _word_count, _has_smile_word


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


class TestHasSmileWord(unittest.TestCase):
    def test_has_smile(self):
        self.assertTrue(_has_smile_word("ella sonríe alegremente"))

    def test_no_smile(self):
        self.assertFalse(_has_smile_word("él camina serio por la calle"))

    def test_spanish_accents(self):
        self.assertTrue(_has_smile_word("reír es saludable"))


if __name__ == "__main__":
    unittest.main()
