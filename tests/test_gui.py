import os
import time
from pathlib import Path

import pytest

from dedupekit.core import ScanOptions, ScanResult, scan_paths, Cancelled
from dedupekit.files import FileInfo
from dedupekit.gui_logic import GroupVM, build_groups, human, remark, summary, to_plans


def fi(name, size=100, mtime=1.0):
    return FileInfo(Path(name), size, mtime, (0, 0))


def result(exact=(), images=(), videos=()):
    return ScanResult(10, list(exact), list(images), list(videos))


def test_grupo_exato_vem_marcado_e_sempre_sobra_um():
    r = result(exact=[[fi("a.jpg", 300), fi("b.jpg", 100), fi("c.jpg", 200)]])
    g = build_groups(r, "largest")[0]
    assert {p.name for p in g.remove} == {"b.jpg", "c.jpg"}          # a.jpg (largest) is kept
    # the last kept file can never be removed:
    g.remove = {Path("b.jpg")}
    assert g.toggle(Path("a.jpg")) is True                            # c.jpg still kept
    assert g.toggle(Path("c.jpg")) is False                           # would leave nothing
    assert len([f for f in g.files if f.path not in g.remove]) == 1


def test_grupo_parecido_comeca_sem_marcas_a_menos_que_o_usuario_peca():
    r = result(images=[[fi("x.jpg", 50), fi("y.jpg", 90)]])
    assert build_groups(r, "largest")[0].remove == set()
    marked = build_groups(r, "largest", mark_similar=True)[0]
    assert {p.name for p in marked.remove} == {"x.jpg"}


def test_remark_respeita_a_politica_e_limpa_os_parecidos():
    r = result(exact=[[fi("a", 1, 5.0), fi("b", 2, 9.0)]], images=[[fi("x"), fi("y", 200)]])
    groups = build_groups(r, "oldest", mark_similar=True)
    assert {p.name for p in groups[0].remove} == {"b"}
    remark(groups, "newest", mark_similar=False)
    assert {p.name for p in groups[0].remove} == {"a"} and groups[1].remove == set()


def test_resumo_e_planos():
    r = result(exact=[[fi("a", 300), fi("b", 100)]], images=[[fi("x", 10), fi("y", 20)]])
    groups = build_groups(r, "largest", mark_similar=True)
    n, size, similar = summary(groups)
    assert (n, size, similar) == (2, 110, 1)
    plans = to_plans(groups)
    assert len(plans) == 2 and all(p.keep.path not in {f.path for f in p.remove} for p in plans)


def test_to_plans_ignora_grupo_sem_marcas():
    groups = [GroupVM("exact", [fi("a"), fi("b")])]
    assert to_plans(groups) == []


def test_human():
    assert human(512) == "512 B" and human(2048) == "2.0 KB" and human(5 * 1024 ** 3) == "5.0 GB"


def test_cancelar_a_busca_interrompe(tmp_path):
    for i in range(30):
        (tmp_path / f"{i}.bin").write_bytes(b"z" * 5000)

    def stop(stage, n):
        raise Cancelled()
    with pytest.raises(Cancelled):
        scan_paths([tmp_path], ScanOptions(), stop)


def test_scan_paths_fim_a_fim(tmp_path):
    (tmp_path / "a.bin").write_bytes(b"k" * 4000)
    (tmp_path / "b.bin").write_bytes(b"k" * 4000)
    res = scan_paths([tmp_path], ScanOptions())
    assert res.files_scanned == 2 and len(res.exact) == 1


# ── janela de verdade (pulado sem tela) ────────────────────────────────────────
def _app_or_skip():
    import tkinter as tk
    try:
        from dedupekit.gui import App
        return App()
    except tk.TclError:
        pytest.skip("no display available for Tk")


def test_janela_abre_mostra_grupos_e_limpa(tmp_path, monkeypatch):
    app = _app_or_skip()
    try:
        from tkinter import messagebox
        monkeypatch.setattr(messagebox, "askyesno", lambda *a, **k: True)
        monkeypatch.setattr(messagebox, "showinfo", lambda *a, **k: None)
        (tmp_path / "a.bin").write_bytes(b"k" * 4000)
        (tmp_path / "b.bin").write_bytes(b"k" * 4000)
        app.paths = [tmp_path]
        app.quarantine.set(str(tmp_path / "q"))
        app._show(scan_paths([tmp_path], ScanOptions()))
        assert len(app.groups) == 1 and len(app.iid_map) == 2
        assert "1 arquivo(s) marcados" in app.summary_lbl.cget("text")
        # toggling the last kept file is refused
        kept = [iid for iid, (gi, p) in app.iid_map.items() if not app.groups[gi].is_removed(p)][0]
        app._toggle(kept)
        assert len([1 for g in app.groups for f in g.files if f.path not in g.remove]) == 1
        app.clean()
        assert len(list(tmp_path.glob("*.bin"))) == 1 and len(list((tmp_path / "q").rglob("*.bin"))) == 1
    finally:
        app.destroy()
