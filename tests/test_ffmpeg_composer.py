"""
Tests para ffmpeg_composer.py — verifican que los filtros FFmpeg sean syntácticamente válidos.
Usa un video sintético generado con testsrc2 de FFmpeg.
"""
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from ffmpeg_composer import ensure_1080x1920, draw_zocalo, process_video, ZocaloConfig


class TestFFmpegComposer(unittest.TestCase):
    """Pruebas de composición FFmpeg usando video sintético."""

    @classmethod
    def setUpClass(cls):
        """Genera un video sintético de 8 segundos (testsrc2) una vez."""
        cls.tmpdir = tempfile.mkdtemp(prefix="ara_test_")
        cls.testsrc_path = Path(cls.tmpdir) / "testsrc.mp4"
        rc, out, err = subprocess.run(
            ["ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc2=size=1920x1080:duration=8:rate=30",
             "-c:v", "libx264", "-pix_fmt", "yuv420p", "-an",
             str(cls.testsrc_path)],
            capture_output=True, text=True
        ).returncode, "", ""
        cls.testsrc_exists = cls.testsrc_path.exists()

    def test_ensure_1080x1920_from_horizontal(self):
        """Escala un video horizontal a 1080x1920 vertical."""
        if not self.testsrc_exists:
            self.skipTest("Video testsrc no disponible")
        out = Path(self.tmpdir) / "scaled.mp4"
        ok = ensure_1080x1920(str(self.testsrc_path), str(out))
        if not ok:
            self.fail("ensure_1080x1920 devolvió False")
        self.assertTrue(out.exists(), "Archivo de salida no creado")
        # Verificar dimensiones
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=width,height",
             "-of", "csv=p=0", str(out)],
            capture_output=True, text=True
        )
        dims = probe.stdout.strip().split(',')
        self.assertEqual(dims, ['1080', '1920'], f"Dimensiones incorrectas: {dims}")

    def test_draw_zocalo_basic(self):
        """Dibuja zócalo básico sin errores."""
        if not self.testsrc_exists:
            self.skipTest("Video testsrc no disponible")
        scaled = Path(self.tmpdir) / "scaled.mp4"
        if not scaled.exists():
            ensure_1080x1920(str(self.testsrc_path), str(scaled))
        out = Path(self.tmpdir) / "zocalo.mp4"
        cfg = ZocaloConfig(volanta_text="Ara · SV", titulo_text="Título de prueba largo que cabe en dos líneas")
        ok = draw_zocalo(str(scaled), str(out), cfg, duration_sec=4.0)
        self.assertTrue(ok, "draw_zocalo falló")
        self.assertTrue(out.exists())

    def test_process_video_full_pipeline(self):
        """Pipeline completo: scale → zócalo → audio normalize."""
        if not self.testsrc_exists:
            self.skipTest("Video testsrc no disponible")
        out = Path(self.tmpdir) / "final.mp4"
        result = process_video(
            str(self.testsrc_path),
            str(out),
            volanta_text="Saladillo Vivo",
            titulo_text="Primera noticia generada por la factoría Ara",
        )
        self.assertTrue(result['ok'], f"Pipeline falló: {result}")
        self.assertTrue(out.exists(), "Video final no generado")
        # Verificar dims 1080x1920
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=width,height",
             "-of", "csv=p=0", str(out)],
            capture_output=True, text=True
        )
        dims = probe.stdout.strip().split(',')
        self.assertEqual(dims, ['1080', '1920'], f"Dim incorrectas: {dims}")
        print(f"\n✅ Video final: {out} ({out.stat().st_size / 1024:.1f} KB)")


if __name__ == "__main__":
    unittest.main()
