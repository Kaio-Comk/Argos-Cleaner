import json
import os
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pytest
from PIL import Image

from twinfinder.takeout import (ApplyOptions, Sidecar, apply_takeout, candidate_names, exiftool_args,
                                find_exiftool, match_dir, parse_sidecar, scan_takeout, set_file_times)

EXIFTOOL = os.environ.get("EXIFTOOL_BIN") or shutil.which("exiftool")
TAKEN = 1651263096                      # 2022-04-29 20:11:36 UTC


def side(folder: Path, name: str, *, title=None, ts=TAKEN, geo=True, desc="") -> Path:
    data = {"title": title if title is not None else name.split(".supplemental")[0],
            "description": desc, "photoTakenTime": {"timestamp": str(ts)},
            "geoData": {"latitude": -3.6972361, "longitude": -40.34805, "altitude": 65.0} if geo
            else {"latitude": 0.0, "longitude": 0.0, "altitude": 0.0}}
    p = folder / name
    p.write_text(json.dumps(data), encoding="utf-8")
    return p


def photo(path: Path, color=(200, 30, 30)):
    Image.new("RGB", (64, 48), color).save(path, "JPEG")
    return path


# ── parse ──────────────────────────────────────────────────────────────────────
def test_parse_sidecar_data_gps_e_descricao(tmp_path):
    p = side(tmp_path, "a.jpg.supplemental-metadata.json", desc=" viagem ")
    sc = parse_sidecar(p)
    assert sc.taken == datetime(2022, 4, 29, 20, 11, 36, tzinfo=timezone.utc)
    assert (sc.lat, sc.lon, sc.alt) == (-3.6972361, -40.34805, 65.0) and sc.description == "viagem"


def test_parse_ignora_gps_zerado_e_json_estranho(tmp_path):
    assert parse_sidecar(side(tmp_path, "b.jpg.supplemental-metadata.json", geo=False)).lat is None
    (tmp_path / "x.json").write_text('{"nada": 1}', encoding="utf-8")
    (tmp_path / "y.json").write_text("not json", encoding="utf-8")
    assert parse_sidecar(tmp_path / "x.json") is None and parse_sidecar(tmp_path / "y.json") is None


# ── pareamento ─────────────────────────────────────────────────────────────────
def test_candidatos_cobrem_as_manhas_do_takeout():
    c = candidate_names("IMG_1.jpg")
    assert c[0] == "img_1.jpg.supplemental-metadata.json" and "img_1.jpg.json" in c
    assert "a.jpg.supplemental-metadata(1).json" in candidate_names("a(1).jpg")      # bracket swap
    assert "a.jpg.supplemental-metadata.json" in candidate_names("a-edited.jpg")      # edited copy
    assert "20030616.json" in candidate_names("20030616.jpg")                         # no extension


def _dir(files):
    return list(files)


def test_match_dir_pares_orfaos_e_midia_sem_json(tmp_path):
    photo(tmp_path / "a.jpg"); side(tmp_path, "a.jpg.supplemental-metadata.json")
    photo(tmp_path / "solta.jpg")                                       # media with no sidecar
    side(tmp_path, "sumiu.jpg.supplemental-metadata.json")              # sidecar with no media
    pairs, rest, orphans = match_dir(list(tmp_path.iterdir()))
    assert [(m.name, s.name) for m, s in pairs] == [("a.jpg", "a.jpg.supplemental-metadata.json")]
    assert [m.name for m in rest] == ["solta.jpg"] and [o.name for o in orphans] == ["sumiu.jpg.supplemental-metadata.json"]


def test_match_json_antigo_numero_trocado_editada_e_nome_truncado(tmp_path):
    photo(tmp_path / "velho.jpg"); side(tmp_path, "velho.jpg.json")                       # legacy name
    photo(tmp_path / "foto(1).jpg"); side(tmp_path, "foto.jpg.supplemental-metadata(1).json")
    photo(tmp_path / "praia.jpg"); side(tmp_path, "praia.jpg.supplemental-metadata.json")
    photo(tmp_path / "praia-edited.jpg")                                                  # shares praia's
    long = "x" * 50 + ".jpg"
    photo(tmp_path / long)
    trunc = (long + ".supplemental-metadata")[:46] + ".json"                               # Google truncation
    side(tmp_path, trunc, title=long)
    pairs, rest, orphans = match_dir(list(tmp_path.iterdir()))
    got = {m.name: s.name for m, s in pairs}
    assert got["velho.jpg"] == "velho.jpg.json"
    assert got["foto(1).jpg"] == "foto.jpg.supplemental-metadata(1).json"
    assert got["praia-edited.jpg"] == "praia.jpg.supplemental-metadata.json" and got["praia.jpg"]
    assert got[long] == trunc
    assert rest == [] and orphans == []


def test_match_por_titulo_como_ultimo_recurso_e_titulo_ambiguo_nao_chuta(tmp_path):
    photo(tmp_path / "IMG_9.jpg")
    side(tmp_path, "qualquer-nome.json", title="IMG_9.jpg")
    photo(tmp_path / "dup.jpg")
    side(tmp_path, "um.json", title="dup.jpg"); side(tmp_path, "dois.json", title="dup.jpg")
    pairs, rest, _ = match_dir(list(tmp_path.iterdir()))
    assert [m.name for m, _s in pairs] == ["IMG_9.jpg"] and [m.name for m in rest] == ["dup.jpg"]


def test_scan_takeout_recursivo(tmp_path):
    (tmp_path / "sub").mkdir()
    photo(tmp_path / "sub" / "a.jpg"); side(tmp_path / "sub", "a.jpg.supplemental-metadata.json")
    res = scan_takeout([tmp_path])
    assert len(res.pairs) == 1 and res.orphan_sidecars == [] and res.media_without_sidecar == []


# ── aplicar ────────────────────────────────────────────────────────────────────
def test_exiftool_args_imagem_video_e_so_se_faltar():
    sc = Sidecar(Path("x.json"), taken=datetime(2022, 4, 29, 20, 11, 36, tzinfo=timezone.utc),
                 lat=-3.5, lon=-40.25, alt=10.0, description="oi")
    img = exiftool_args(sc, "image")
    assert img[:2] == ["-wm", "cg"] and "-DateTimeOriginal=2022:04:29 20:11:36" in img
    assert "-GPSLatitude=3.5" in img and "-GPSLatitudeRef=S" in img and "-GPSLongitudeRef=W" in img
    assert "-ImageDescription=oi" in img
    vid = exiftool_args(sc, "video")
    assert "-QuickTime:CreateDate=2022:04:29 20:11:36" in vid and not any(a.startswith("-GPS") for a in vid)
    assert "-wm" not in exiftool_args(sc, "image", only_missing=False)


def test_set_file_times(tmp_path):
    f = tmp_path / "a.jpg"; f.write_bytes(b"x")
    set_file_times(f, datetime(2022, 4, 29, 20, 11, 36, tzinfo=timezone.utc))
    assert int(f.stat().st_mtime) == TAKEN


def test_dry_run_nao_muda_nada_e_apply_define_datas_e_move_jsons(tmp_path):
    photo(tmp_path / "a.jpg"); j = side(tmp_path, "a.jpg.supplemental-metadata.json")
    before = (tmp_path / "a.jpg").stat().st_mtime
    scan = scan_takeout([tmp_path])
    opts = ApplyOptions(set_dates=True, move_sidecars_to=tmp_path / "_meta")
    rep = apply_takeout(scan, opts, apply=False)
    assert (rep.dates_set, rep.sidecars_moved) == (1, 1) and (tmp_path / "a.jpg").stat().st_mtime == before and j.exists()
    rep = apply_takeout(scan, opts, apply=True)
    assert int((tmp_path / "a.jpg").stat().st_mtime) == TAKEN and not j.exists()
    assert (tmp_path / "_meta" / j.name).exists() and rep.errors == []


def test_exif_sem_exiftool_levanta_erro_claro(tmp_path, monkeypatch):
    monkeypatch.setattr("twinfinder.takeout.find_exiftool", lambda explicit=None: None)
    with pytest.raises(RuntimeError, match="ExifTool not found"):
        apply_takeout(scan_takeout([tmp_path]), ApplyOptions(exif=True), apply=True)


def test_falha_do_exiftool_nao_move_o_json(tmp_path, monkeypatch):
    photo(tmp_path / "a.jpg"); j = side(tmp_path, "a.jpg.supplemental-metadata.json")
    monkeypatch.setattr("twinfinder.takeout.find_exiftool", lambda explicit=None: "fake")
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 1, "", "boom"))
    rep = apply_takeout(scan_takeout([tmp_path]), ApplyOptions(exif=True, move_sidecars_to=tmp_path / "m"), apply=True)
    assert rep.sidecars_moved == 0 and j.exists() and "boom" in rep.errors[0]


@pytest.mark.skipif(not EXIFTOOL, reason="exiftool not installed")
def test_exiftool_de_verdade_copia_com_metadados_e_original_intacto(tmp_path):
    src = tmp_path / "in"; src.mkdir()
    photo(src / "a.jpg"); side(src, "a.jpg.supplemental-metadata.json", desc="praia")
    original = (src / "a.jpg").read_bytes()
    out = tmp_path / "out"
    rep = apply_takeout(scan_takeout([src]), ApplyOptions(exif=True, exiftool=EXIFTOOL, out_dir=out), apply=True)
    assert rep.errors == [] and (src / "a.jpg").read_bytes() == original          # original untouched
    tags = subprocess.run([EXIFTOOL, "-s3", "-DateTimeOriginal", "-GPSLatitude", "-ImageDescription",
                           "-n", str(out / "a.jpg")], capture_output=True, text=True).stdout.split("\n")
    assert tags[0] == "2022:04:29 20:11:36" and float(tags[1]) == pytest.approx(-3.6972361) and tags[2] == "praia"


@pytest.mark.skipif(not EXIFTOOL, reason="exiftool not installed")
def test_exiftool_nao_sobrescreve_data_que_ja_existe(tmp_path):
    f = photo(tmp_path / "a.jpg"); side(tmp_path, "a.jpg.supplemental-metadata.json")
    subprocess.run([EXIFTOOL, "-overwrite_original", "-DateTimeOriginal=2001:01:01 00:00:00", str(f)], check=True,
                   capture_output=True)
    apply_takeout(scan_takeout([tmp_path]), ApplyOptions(exif=True, exiftool=EXIFTOOL,
                                                         keep_exiftool_backup=False), apply=True)
    got = subprocess.run([EXIFTOOL, "-s3", "-DateTimeOriginal", str(f)], capture_output=True, text=True).stdout.strip()
    assert got == "2001:01:01 00:00:00"
