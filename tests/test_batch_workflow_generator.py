"""
Tests unitarios para batch_workflow_generator.py (FASE 4 - refactor).
Cobertura: generación de JSON batch, encadenamiento de clips, rutas guardadas.
"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
import batch_workflow_generator as bwg  # noqa: E402


class TestBatchWorkflowGenerator(unittest.TestCase):
    """Tests para el generador unificado de workflows batch."""

    def _make_ara_json(self, guion="Noticia de última hora sobre el clima en Saladillo con pronóstico de lluvia para el fin de semana"):
        return {
            "version": "1.1",
            "contenido": {
                "guion_ara": guion,
                "seccion_a_guion": "Pronóstico para Saladillo indica lluvia intensa este sábado por la tarde.",
                "seccion_b_guion": "Autoridades recomiendan evitar zonas bajas y mantenerse informado sobrealertas meteorológicas.",
                "volanta_zocalo": "Aviso meteorológico",
                "titulo_zocalo": "Lluvia fuerte golpea Saladillo este fin de semana",
            },
            "meta_ara": {
                "tenor": "informativo_servicio",
                "permitir_sonrisa": True,
                "tono_vocal": "Voz femenina joven y clara",
                "expresion_inicio": "Mirada fija al lente con leve asentimiento",
                "expresion_desarrollo": "Expresión serena natural",
                "expresion_cierre": "Último guiño sutil",
                "lenguaje_corporal": "postura_firme_contenida",
                "ambiente_sonoro": "Sonido de calle urbana",
                "ritmo_camara": "plano_fijo_estable",
            },
        }

    # -----------------------------------------------------------------------
    # Tests de generate_batch_json
    # -----------------------------------------------------------------------

    def test_genera_dos_clips(self):
        ara = self._make_ara_json()
        batch = bwg.generate_batch_json(ara_json_input=ara, article_id=901)
        jobs = batch.get("jobs", [])
        self.assertEqual(len(jobs), 2, f"Se esperaban 2 jobs, se obtuvo: {[j['name'] for j in jobs]}")

    def test_jobs_tienen_tipo_video(self):
        ara = self._make_ara_json()
        batch = bwg.generate_batch_json(ara_json_input=ara, article_id=901)
        for job in batch["jobs"]:
            self.assertEqual(job["type"], "video", f"Job {job['name']} no es tipo video")

    def test_encadenamiento_start_clip2(self):
        ara = self._make_ara_json()
        batch = bwg.generate_batch_json(ara_json_input=ara, article_id=901)
        jobs = batch["jobs"]
        clip1_name = jobs[0]["name"]
        clip2 = jobs[1]
        self.assertIn("start", clip2, "Clip 2 debería tener campo 'start' para continuidad")
        self.assertTrue(
            clip2["start"].endswith(".mp4"),
            f"Start de clip2 debe terminar en .mp4, obtuvo: {clip2['start']}"
        )
        self.assertIn(clip1_name, clip2["start"])

    def test_nombres_unicos_por_articulo(self):
        ara = self._make_ara_json()
        batch = bwg.generate_batch_json(ara_json_input=ara, article_id=850)
        names = [j["name"] for j in batch["jobs"]]
        self.assertEqual(len(names), len(set(names)), "Los nombres de jobs deben ser únicos")

    def test_project_name_prefijo(self):
        ara = self._make_ara_json()
        batch = bwg.generate_batch_json(ara_json_input=ara, article_id=777, project_name="test777")
        self.assertEqual(batch["project"], "test777")

    def test_defaults_ratio_y_modelo(self):
        ara = self._make_ara_json()
        batch = bwg.generate_batch_json(ara_json_input=ara, article_id=901)
        defaults = batch.get("defaults", {})
        self.assertEqual(defaults.get("ratio"), "9:16")
        self.assertEqual(defaults.get("video_model"), "Veo 3.1 - Lite")

    def test_prompts_no_vacios(self):
        ara = self._make_ara_json()
        batch = bwg.generate_batch_json(ara_json_input=ara, article_id=901)
        for job in batch["jobs"]:
            prompt = job.get("prompt", "")
            self.assertGreater(len(prompt.strip()), 10, f"Prompt vacío o muy corto para {job['name']}")

    def test_articulo_id_default_usa_timestamp(self):
        ara = self._make_ara_json()
        batch = bwg.generate_batch_json(ara_json_input=ara)  # sin article_id
        self.assertIn("batch_", batch["project"])

    def test_n_clips_respeta_prompts_generados(self):
        """Si el assembler genera N prompts, el batch JSON tiene N jobs."""
        ara = self._make_ara_json()  # tiene 2 secciones -> 2 clips
        batch = bwg.generate_batch_json(ara_json_input=ara, n_clips=5)  # pido 5 pero hay 2 secciones
        self.assertEqual(len(batch["jobs"]), 2, "Deben generarse 2 jobs por las 2 secciones existentes")

    # -----------------------------------------------------------------------
    # Tests de save_batch_json
    # -----------------------------------------------------------------------

    def test_save_batch_json_crea_archivo(self):
        with tempfile.TemporaryDirectory() as td:
            data = {"project": "test", "jobs": [{"type": "video"}]}
            out = os.path.join(td, "batch_out.json")
            path = bwg.save_batch_json(data, out)
            self.assertTrue(Path(path).exists())
            with open(path, encoding="utf-8") as f:
                loaded = json.load(f)
            self.assertEqual(loaded["project"], "test")

    def test_save_batch_json_crea_directorio_si_no_existe(self):
        with tempfile.TemporaryDirectory() as td:
            deep = os.path.join(td, "a", "b", "out.json")
            data = {"project": "deep", "jobs": []}
            path = bwg.save_batch_json(data, deep)
            self.assertTrue(Path(path).exists())

    # -----------------------------------------------------------------------
    # Tests de _extract_article_id
    # -----------------------------------------------------------------------

    def test_extract_article_id_dict(self):
        ara = {"contenido": {"article_id": 901}}
        aid = bwg._extract_article_id(ara)
        self.assertEqual(aid, 901)

    def test_extract_article_id_string(self):
        ara_str = '{"contenido": {"id": 888}}'
        aid = bwg._extract_article_id(ara_str)
        self.assertEqual(aid, 888)

    def test_extract_article_id_missing(self):
        ara = {"contenido": {}}
        aid = bwg._extract_article_id(ara)
        self.assertIsNone(aid)

    # -----------------------------------------------------------------------
    # Tests de _get_master_image_ref
    # -----------------------------------------------------------------------

    def test_get_master_image_ref_no_env(self):
        fake_path = Path("/nonexistent/.env")
        with patch.object(bwg.Path, "exists", return_value=False):
            result = bwg._get_master_image_ref()
        self.assertIsNone(result)


class TestMultiClipOrchestratorInit(unittest.TestCase):
    """Tests ligeros de inicialización (sin ejecutar Flow)."""

    def test_init_setea_campos(self):
        from multi_clip_orchestrator import MultiClipOrchestrator  # noqa: E402
        orch = MultiClipOrchestrator(
            article_id=901,
            ara_json={"contenido": {}},
            n_clips=2,
        )
        self.assertEqual(orch.article_id, 901)
        self.assertEqual(orch.project_name, "a901")
        self.assertEqual(orch.n_clips, 2)
        self.assertIsNone(orch.batch_json_path)
        self.assertEqual(orch.generated_clips, [])


if __name__ == "__main__":
    unittest.main()
