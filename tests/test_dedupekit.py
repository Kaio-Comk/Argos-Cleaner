import os
import random
import shutil
import subprocess
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from dedupekit.actions import apply_plans, make_plans, pick_keeper
from dedupekit.cli import main
from dedupekit.exact import find_exact
from dedupekit.files import walk
from dedupekit.perceptual import (dhash_pixels, find_similar_images, group_similar, hamming)

FFMPEG = os.environ.get("FFMPEG_BIN") or shutil.which("ffmpeg")
FFPROBE = os.environ.get("FFPROBE_BIN") or shutil.which("ffprobe")


def make_photo(path: Path, seed: int, size=(320, 240), fmt=None, quality=90):
    rnd = random.Random(seed)
    im = Image.new("RGB", size, (rnd.randrange(256), rnd.randrange(256), rnd.randrange(256)))
    d = ImageDraw.Draw(im)
    for _ in range(25):
        x, y = rnd.randrange(size[0]), rnd.randrange(size[1])
        r = rnd.randrange(15, 80)
        d.ellipse([x - r, y - r, x + r, y + r], fill=tuple(rnd.randrange(256) for _ in range(3)))
    im.save(path, fmt, quality=quality) if fmt == "JPEG" else im.save(path, fmt)
    return im


# ── exact ──────────────────────────────────────────────────────────────────────
def test_exact_acha_copia_com_nome_diferente_e_ignora_mesmo_tamanho_diferente(tmp_path):
    (tmp_path / "a.bin").write_bytes(b"x" * 200_000)
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "outro-nome.dat").write_bytes(b"x" * 200_000)
    (tmp_path / "mesmo-tamanho.bin").write_bytes(b"y" * 200_000)       # same size, other content
    (tmp_path / "unico.bin").write_bytes(b"z" * 10)
    groups = find_exact(walk([tmp_path]))
    assert len(groups) == 1
    assert {f.path.name for f in groups[0]} == {"a.bin", "outro-nome.dat"}


def test_exact_diferencia_arquivos_que_so_diferem_no_meio(tmp_path):
    base = bytearray(b"a" * 500_000)
    (tmp_path / "1.bin").write_bytes(bytes(base))
    base[250_000] = ord("b")                                   # first/last 64KB identical!
    (tmp_path / "2.bin").write_bytes(bytes(base))
    assert find_exact(walk([tmp_path])) == []


def test_hardlink_nao_conta_como_duplicata(tmp_path):
    (tmp_path / "a.bin").write_bytes(b"q" * 1000)
    try:
        os.link(tmp_path / "a.bin", tmp_path / "b.bin")
    except OSError:
        pytest.skip("hard links not supported here")
    assert find_exact(walk([tmp_path])) == []


def test_symlink_e_ignorado(tmp_path):
    (tmp_path / "a.bin").write_bytes(b"q" * 1000)
    try:
        os.symlink(tmp_path / "a.bin", tmp_path / "link.bin")
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not available (needs privileges on Windows)")
    assert find_exact(walk([tmp_path])) == []


# ── perceptual ─────────────────────────────────────────────────────────────────
def test_dhash_pixels_e_hamming():
    assert dhash_pixels(bytes(range(72, 0, -1))) == dhash_pixels(bytes(range(72, 0, -1)))
    assert hamming(0b1010, 0b0110) == 2
    with pytest.raises(ValueError):
        dhash_pixels(b"123")


def test_group_similar_usa_indice_por_pedacos_e_acha_so_os_proximos():
    base = 0x0123456789ABCDEF
    near = base ^ 0b101                    # 2 bits away
    far = base ^ ((1 << 40) - 1)           # 40 bits away
    for t in (2, 5, 8):
        groups = group_similar({"a": base, "b": near, "c": far}, t)
        assert [sorted(g) for g in groups] == [["a", "b"]]
    assert group_similar({"a": base, "b": near}, 1) == []      # distance 2 > threshold 1
    with pytest.raises(ValueError):
        group_similar({"a": 1}, 40)


def test_foto_redimensionada_e_recomprimida_e_agrupada_e_outra_foto_nao(tmp_path):
    make_photo(tmp_path / "original.png", seed=1, size=(640, 480))
    Image.open(tmp_path / "original.png").resize((320, 240)).save(tmp_path / "pequena.jpg", quality=70)
    Image.open(tmp_path / "original.png").convert("RGB").save(tmp_path / "copia.bmp")
    make_photo(tmp_path / "diferente.png", seed=99, size=(640, 480))
    groups = find_similar_images(walk([tmp_path]), threshold=6)
    assert len(groups) == 1
    assert {f.path.name for f in groups[0]} == {"original.png", "pequena.jpg", "copia.bmp"}


def test_imagem_corrompida_nao_derruba_a_busca(tmp_path):
    (tmp_path / "quebrada.jpg").write_bytes(b"not an image")
    make_photo(tmp_path / "ok.png", seed=3)
    assert find_similar_images(walk([tmp_path])) == []


# ── escolha e ações ────────────────────────────────────────────────────────────
def _plans(tmp_path, policy="largest"):
    make_photo(tmp_path / "grande.png", seed=5, size=(800, 600))
    Image.open(tmp_path / "grande.png").resize((200, 150)).save(tmp_path / "mini.png")
    (tmp_path / "copia1.bin").write_bytes(b"d" * 5000)
    (tmp_path / "copia2.bin").write_bytes(b"d" * 5000)
    files = list(walk([tmp_path]))
    return files, make_plans(find_exact(files), "exact", policy)


def test_politicas_de_escolha(tmp_path):
    files, _ = _plans(tmp_path)
    group = [f for f in files if f.path.suffix == ".png"]
    biggest = max(group, key=lambda f: f.size)
    smallest = min(group, key=lambda f: f.size)
    assert pick_keeper(group, "largest").path == biggest.path       # by FILE size, not pixels
    assert pick_keeper(group, "smallest").path == smallest.path
    assert pick_keeper(group, "highest-res").path.name == "grande.png"
    with pytest.raises(ValueError):
        pick_keeper(group, "random")


def test_dry_run_nao_mexe_em_nada_e_sempre_sobra_um(tmp_path):
    _, plans = _plans(tmp_path)
    out = apply_plans(plans, roots=[tmp_path], quarantine_dir=tmp_path / "q", apply=False)
    assert out.count("dry-run") == 1 and not (tmp_path / "q").exists()
    assert (tmp_path / "copia1.bin").exists() and (tmp_path / "copia2.bin").exists()


def test_quarentena_move_preservando_caminho_e_mantem_um(tmp_path):
    _, plans = _plans(tmp_path)
    out = apply_plans(plans, roots=[tmp_path], quarantine_dir=tmp_path / "q", apply=True)
    assert out.count("moved") == 1
    remaining = [p for p in ("copia1.bin", "copia2.bin") if (tmp_path / p).exists()]
    assert len(remaining) == 1                                        # one stays
    assert len(list((tmp_path / "q").rglob("*.bin"))) == 1            # one is in quarantine


def test_arquivo_alterado_depois_do_scan_e_pulado(tmp_path):
    _, plans = _plans(tmp_path)
    victim = plans[0].remove[0].path
    victim.write_bytes(b"d" * 5000 + b"mudou")                          # changed after the scan
    out = apply_plans(plans, roots=[tmp_path], quarantine_dir=tmp_path / "q", apply=True)
    assert out.count("skipped") == 1 and victim.exists()


def test_trash_usa_a_funcao_injetada(tmp_path):
    _, plans = _plans(tmp_path)
    sent = []
    out = apply_plans(plans, action="trash", roots=[tmp_path], apply=True, trash=sent.append)
    assert out.count("trashed") == 1 and len(sent) == 1


# ── CLI ────────────────────────────────────────────────────────────────────────
def test_cli_scan_gera_json_e_clean_e_dry_run_por_padrao(tmp_path, capsys):
    (tmp_path / "a.bin").write_bytes(b"k" * 3000)
    (tmp_path / "b.bin").write_bytes(b"k" * 3000)
    out_json = tmp_path.parent / (tmp_path.name + "-out.json")
    assert main(["scan", str(tmp_path), "--json", str(out_json)]) == 0
    assert out_json.exists() and "exact" in out_json.read_text(encoding="utf-8")
    assert main(["clean", str(tmp_path), "--quarantine-dir", str(tmp_path / "q")]) == 0
    assert "Dry run" in capsys.readouterr().out
    assert (tmp_path / "a.bin").exists() and (tmp_path / "b.bin").exists()


def test_cli_apply_e_segunda_rodada_nao_revarre_a_quarentena(tmp_path, capsys):
    (tmp_path / "a.bin").write_bytes(b"k" * 3000)
    (tmp_path / "b.bin").write_bytes(b"k" * 3000)
    q = tmp_path / "q"
    assert main(["clean", str(tmp_path), "--quarantine-dir", str(q), "--apply"]) == 0
    assert len(list(tmp_path.glob("*.bin"))) == 1 and len(list(q.rglob("*.bin"))) == 1
    capsys.readouterr()
    assert main(["clean", str(tmp_path), "--quarantine-dir", str(q), "--apply"]) == 0
    assert "0 group(s)" in capsys.readouterr().out                      # nothing new to do


# ── vídeo (precisa de ffmpeg) ──────────────────────────────────────────────────
needs_ffmpeg = pytest.mark.skipif(not (FFMPEG and FFPROBE), reason="ffmpeg not installed")


def _clip(path: Path, source: str, size="320x240", crf="23"):
    subprocess.run([FFMPEG, "-v", "error", "-f", "lavfi", "-i", f"{source}=size={size}:rate=10", "-t", "4",
                    "-c:v", "libx264", "-crf", crf, "-pix_fmt", "yuv420p", "-y", str(path)], check=True)


@needs_ffmpeg
def test_video_reencodado_e_menor_e_agrupado_e_video_diferente_nao(tmp_path):
    from dedupekit.video import find_similar_videos
    _clip(tmp_path / "original.mp4", "testsrc2", "640x480", "18")
    subprocess.run([FFMPEG, "-v", "error", "-i", str(tmp_path / "original.mp4"), "-vf", "scale=320:240",
                    "-crf", "32", "-y", str(tmp_path / "pequeno.mp4")], check=True)
    _clip(tmp_path / "outro.mp4", "mandelbrot", "640x480")
    groups = find_similar_videos(walk([tmp_path]), threshold=10, ffmpeg=FFMPEG, ffprobe=FFPROBE)
    assert len(groups) == 1
    assert {f.path.name for f in groups[0]} == {"original.mp4", "pequeno.mp4"}


@needs_ffmpeg
def test_video_highest_res_mantem_o_de_maior_resolucao(tmp_path, monkeypatch):
    from dedupekit import video
    monkeypatch.setattr(video, "shutil", shutil)
    monkeypatch.setenv("PATH", str(Path(FFMPEG).parent) + os.pathsep + os.environ["PATH"])
    _clip(tmp_path / "hd.mp4", "testsrc2", "640x480", "18")
    _clip(tmp_path / "sd.mp4", "testsrc2", "160x120", "30")
    files = list(walk([tmp_path]))
    assert pick_keeper(files, "highest-res").path.name == "hd.mp4"
