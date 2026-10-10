"""Desktop window (tkinter, no extra dependencies): scan, review, then clean safely."""
from __future__ import annotations

import os
import queue
import shutil
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Optional

from . import __version__
from .actions import apply_plans
from .core import Cancelled, ScanOptions, ScanResult, scan_paths
from .gui_logic import KIND_LABEL, GroupVM, build_groups, human, remark, summary, to_plans

KEEP_CHOICES = {
    "Maior resolução": "highest-res",
    "Maior arquivo": "largest",
    "Mais antigo": "oldest",
    "Mais novo": "newest",
    "Caminho mais curto": "shortest-path",
}
SENSITIVITY = {                       # (image bits of 64, video bits per frame)
    "Rigorosa (só quase idênticos)": (3, 5.0),
    "Normal": (5, 8.0),
    "Ampla (mais resultados, revise bem)": (8, 12.0),
}
STAGE_TEXT = {"walk": "Lendo pastas", "hash": "Comparando conteúdo", "image": "Analisando fotos",
              "video": "Analisando vídeos"}


def _exe_dir() -> Path:
    return Path(sys.executable).parent if getattr(sys, "frozen", False) else Path.cwd()


def ffmpeg_available() -> bool:
    return bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))


def reveal_in_file_manager(path: Path) -> None:
    try:
        if sys.platform == "win32":
            subprocess.Popen(["explorer", "/select,", str(path)])
        elif sys.platform == "darwin":
            subprocess.Popen(["open", "-R", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path.parent)])
    except OSError:
        pass


class App(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title(f"TwinFinder {__version__} - Achador de fotos e vídeos duplicados")
        self.geometry("1100x700")
        self.minsize(900, 560)
        os.environ["PATH"] = str(_exe_dir()) + os.pathsep + os.environ.get("PATH", "")   # ffmpeg.exe beside us

        self.paths: list[Path] = []
        self.groups: list[GroupVM] = []
        self.iid_map: dict[str, tuple[int, Path]] = {}
        self.events: queue.Queue = queue.Queue()
        self.cancel_flag = threading.Event()
        self.worker: Optional[threading.Thread] = None
        self.preview_ref = None
        self.quarantine = tk.StringVar(value=str(Path.home() / "twinfinder-quarentena"))
        self.vars = {
            "images": tk.BooleanVar(value=True), "videos": tk.BooleanVar(value=False),
            "mark_similar": tk.BooleanVar(value=False), "action": tk.StringVar(value="quarantine"),
            "sens": tk.StringVar(value="Normal"), "keep": tk.StringVar(value="Maior resolução"),
        }
        self._build()
        self._build_menu()
        self.after(100, self._poll)

    def _build_menu(self) -> None:
        menubar = tk.Menu(self)
        tools = tk.Menu(menubar, tearoff=0)
        tools.add_command(label="Corrigir Google Photos Takeout...", command=self.open_takeout)
        menubar.add_cascade(label="Ferramentas", menu=tools)
        self.config(menu=menubar)

    def open_takeout(self) -> None:
        from .takeout_gui import TakeoutWindow
        TakeoutWindow(self)

    # ── layout ─────────────────────────────────────────────────────────────────
    def _build(self) -> None:
        pad = {"padx": 8, "pady": 4}
        top = ttk.LabelFrame(self, text="1. Pastas para analisar")
        top.pack(fill="x", **pad)
        self.lb = tk.Listbox(top, height=3)
        self.lb.pack(side="left", fill="x", expand=True, padx=6, pady=6)
        col = ttk.Frame(top)
        col.pack(side="left", padx=6)
        ttk.Button(col, text="Adicionar pasta…", command=self.add_folder).pack(fill="x", pady=2)
        ttk.Button(col, text="Remover", command=self.remove_folder).pack(fill="x", pady=2)

        opt = ttk.LabelFrame(self, text="2. Opções")
        opt.pack(fill="x", **pad)
        ttk.Checkbutton(opt, text="Fotos parecidas (mesma foto redimensionada/recomprimida)",
                        variable=self.vars["images"]).grid(row=0, column=0, sticky="w", padx=6, pady=2)
        self.chk_video = ttk.Checkbutton(opt, text="Vídeos parecidos (precisa do ffmpeg)",
                                         variable=self.vars["videos"])
        self.chk_video.grid(row=1, column=0, sticky="w", padx=6, pady=2)
        if not ffmpeg_available():
            self.chk_video.state(["disabled"])
            self.vars["videos"].set(False)
            ttk.Label(opt, text="ffmpeg não encontrado: ponha ffmpeg.exe e ffprobe.exe ao lado deste programa.",
                      foreground="#a15c00").grid(row=2, column=0, columnspan=3, sticky="w", padx=6)
        ttk.Label(opt, text="Sensibilidade:").grid(row=0, column=1, sticky="e", padx=6)
        ttk.Combobox(opt, textvariable=self.vars["sens"], values=list(SENSITIVITY), state="readonly",
                     width=34).grid(row=0, column=2, sticky="w")
        ttk.Label(opt, text="Manter o:").grid(row=1, column=1, sticky="e", padx=6)
        cb = ttk.Combobox(opt, textvariable=self.vars["keep"], values=list(KEEP_CHOICES),
                          state="readonly", width=34)
        cb.grid(row=1, column=2, sticky="w")
        cb.bind("<<ComboboxSelected>>", lambda e: self._remark())
        opt.columnconfigure(3, weight=1)

        run = ttk.Frame(self)
        run.pack(fill="x", **pad)
        self.btn_scan = ttk.Button(run, text="Procurar duplicados", command=self.start_scan)
        self.btn_scan.pack(side="left")
        self.btn_cancel = ttk.Button(run, text="Cancelar", command=self.cancel_scan, state="disabled")
        self.btn_cancel.pack(side="left", padx=6)
        self.bar = ttk.Progressbar(run, mode="indeterminate", length=220)
        self.bar.pack(side="left", padx=8)
        self.status = ttk.Label(run, text="Escolha uma ou mais pastas e clique em “Procurar duplicados”.")
        self.status.pack(side="left", padx=8)

        mid = ttk.LabelFrame(self, text="3. Revise: dê dois cliques num arquivo para alternar Manter/Remover")
        mid.pack(fill="both", expand=True, **pad)
        cols = ("size", "kind", "action")
        self.tree = ttk.Treeview(mid, columns=cols, show="tree headings", selectmode="browse")
        self.tree.heading("#0", text="Arquivo")
        self.tree.heading("size", text="Tamanho")
        self.tree.heading("kind", text="Tipo")
        self.tree.heading("action", text="O que fazer")
        self.tree.column("#0", width=560)
        self.tree.column("size", width=90, anchor="e")
        self.tree.column("kind", width=80)
        self.tree.column("action", width=90)
        self.tree.tag_configure("remove", foreground="#b00020")
        self.tree.tag_configure("keep", foreground="#1b6e2d")
        self.tree.tag_configure("group", font=("Segoe UI", 9, "bold"))
        sb = ttk.Scrollbar(mid, command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="left", fill="y")
        self.preview = ttk.Label(mid, text="Selecione um arquivo\npara ver a prévia", anchor="center",
                                 width=40, justify="center")
        self.preview.pack(side="left", fill="y", padx=8)
        self.tree.bind("<Double-1>", self._on_double)
        self.tree.bind("<<TreeviewSelect>>", self._on_select)
        self.tree.bind("<Button-3>", self._on_right)
        self.menu = tk.Menu(self, tearoff=0)
        self.menu.add_command(label="Alternar Manter/Remover", command=self._toggle_selected)
        self.menu.add_command(label="Mostrar na pasta", command=self._reveal_selected)

        bot = ttk.LabelFrame(self, text="4. Limpar (nada é apagado de vez)")
        bot.pack(fill="x", **pad)
        ttk.Checkbutton(bot, text="Marcar também os grupos “parecidos” automaticamente",
                        variable=self.vars["mark_similar"], command=self._remark).grid(
            row=0, column=0, columnspan=3, sticky="w", padx=6, pady=2)
        ttk.Radiobutton(bot, text="Mover para a quarentena:", value="quarantine",
                        variable=self.vars["action"]).grid(row=1, column=0, sticky="w", padx=6)
        ttk.Entry(bot, textvariable=self.quarantine, width=60).grid(row=1, column=1, sticky="we")
        ttk.Button(bot, text="Escolher…", command=self.pick_quarantine).grid(row=1, column=2, padx=6)
        ttk.Radiobutton(bot, text="Enviar para a Lixeira", value="trash",
                        variable=self.vars["action"]).grid(row=2, column=0, sticky="w", padx=6)
        self.summary_lbl = ttk.Label(bot, text="")
        self.summary_lbl.grid(row=3, column=0, columnspan=2, sticky="w", padx=6, pady=4)
        self.btn_clean = ttk.Button(bot, text="Limpar selecionados…", command=self.clean, state="disabled")
        self.btn_clean.grid(row=3, column=2, padx=6, pady=4)
        bot.columnconfigure(1, weight=1)

    # ── folders ────────────────────────────────────────────────────────────────
    def add_folder(self) -> None:
        d = filedialog.askdirectory(title="Escolha uma pasta")
        if d and Path(d) not in self.paths:
            self.paths.append(Path(d))
            self.lb.insert("end", d)

    def remove_folder(self) -> None:
        for i in reversed(self.lb.curselection()):
            self.lb.delete(i)
            del self.paths[i]

    def pick_quarantine(self) -> None:
        d = filedialog.askdirectory(title="Pasta de quarentena")
        if d:
            self.quarantine.set(d)

    # ── scan ───────────────────────────────────────────────────────────────────
    def start_scan(self) -> None:
        if not self.paths:
            messagebox.showinfo("TwinFinder", "Adicione pelo menos uma pasta.")
            return
        img_t, vid_t = SENSITIVITY[self.vars["sens"].get()]
        opts = ScanOptions(similar_images=self.vars["images"].get(), similar_videos=self.vars["videos"].get(),
                           image_threshold=img_t, video_threshold=vid_t,
                           exclude=[Path(self.quarantine.get())])
        self.cancel_flag.clear()
        self._set_busy(True)
        counts = {"n": 0}

        def progress(stage: str, n: int) -> None:
            if self.cancel_flag.is_set():
                raise Cancelled()
            counts["n"] += n
            self.events.put(("progress", stage, counts["n"]))

        def work() -> None:
            try:
                self.events.put(("done", scan_paths(list(self.paths), opts, progress)))
            except Cancelled:
                self.events.put(("cancelled",))
            except Exception as exc:  # noqa: BLE001 - report any failure to the user
                self.events.put(("error", f"{type(exc).__name__}: {exc}"))

        self.worker = threading.Thread(target=work, daemon=True)
        self.worker.start()

    def cancel_scan(self) -> None:
        self.cancel_flag.set()
        self.status.config(text="Cancelando…")

    def _set_busy(self, busy: bool) -> None:
        self.btn_scan.config(state="disabled" if busy else "normal")
        self.btn_cancel.config(state="normal" if busy else "disabled")
        self.btn_clean.config(state="disabled")
        if busy:
            self.bar.start(12)
        else:
            self.bar.stop()

    def _poll(self) -> None:
        try:
            while True:
                ev = self.events.get_nowait()
                if ev[0] == "progress":
                    self.status.config(text=f"{STAGE_TEXT.get(ev[1], ev[1])}… ({ev[2]} itens)")
                elif ev[0] == "done":
                    self._set_busy(False)
                    self._show(ev[1])
                elif ev[0] == "cancelled":
                    self._set_busy(False)
                    self.status.config(text="Busca cancelada.")
                elif ev[0] == "error":
                    self._set_busy(False)
                    self.status.config(text="Erro na busca.")
                    messagebox.showerror("TwinFinder", f"A busca falhou:\n{ev[1]}")
        except queue.Empty:
            pass
        self.after(100, self._poll)

    # ── results ────────────────────────────────────────────────────────────────
    def _policy(self) -> str:
        return KEEP_CHOICES[self.vars["keep"].get()]

    def _show(self, result: ScanResult) -> None:
        self.groups = build_groups(result, self._policy(), mark_similar=self.vars["mark_similar"].get())
        self._render()
        total = len(self.groups)
        self.status.config(text=f"{result.files_scanned} arquivos analisados, {total} grupo(s) de duplicados.")
        if not total:
            messagebox.showinfo("TwinFinder", "Nenhum duplicado encontrado. 🎉")

    def _remark(self) -> None:
        if self.groups:
            remark(self.groups, self._policy(), mark_similar=self.vars["mark_similar"].get())
            self._render()

    def _render(self) -> None:
        self.tree.delete(*self.tree.get_children())
        self.iid_map.clear()
        for gi, g in enumerate(self.groups):
            parent = self.tree.insert("", "end", text=f"Grupo {gi + 1}: {len(g.files)} arquivos",
                                      values=("", KIND_LABEL[g.kind], ""), open=True, tags=("group",))
            for fi in g.files:
                iid = self.tree.insert(parent, "end", text=str(fi.path),
                                       values=(human(fi.size), KIND_LABEL[g.kind],
                                               "Remover" if g.is_removed(fi.path) else "Manter"),
                                       tags=("remove" if g.is_removed(fi.path) else "keep",))
                self.iid_map[iid] = (gi, fi.path)
        self._update_summary()

    def _update_summary(self) -> None:
        n, size, similar = summary(self.groups)
        extra = f" ({similar} de grupos “parecidos”)" if similar else ""
        self.summary_lbl.config(text=f"{n} arquivo(s) marcados para remover{extra}, liberando {human(size)}.")
        self.btn_clean.config(state="normal" if n else "disabled")

    def _toggle(self, iid: str) -> None:
        if iid not in self.iid_map:
            return
        gi, path = self.iid_map[iid]
        if not self.groups[gi].toggle(path):
            messagebox.showinfo("TwinFinder", "Pelo menos um arquivo de cada grupo precisa ser mantido.")
            return
        removed = self.groups[gi].is_removed(path)
        self.tree.item(iid, tags=("remove" if removed else "keep",))
        self.tree.set(iid, "action", "Remover" if removed else "Manter")
        self._update_summary()

    def _on_double(self, event) -> None:
        iid = self.tree.identify_row(event.y)
        if iid:
            self._toggle(iid)

    def _toggle_selected(self) -> None:
        sel = self.tree.selection()
        if sel:
            self._toggle(sel[0])

    def _reveal_selected(self) -> None:
        sel = self.tree.selection()
        if sel and sel[0] in self.iid_map:
            reveal_in_file_manager(self.iid_map[sel[0]][1])

    def _on_right(self, event) -> None:
        iid = self.tree.identify_row(event.y)
        if iid in self.iid_map:
            self.tree.selection_set(iid)
            self.menu.tk_popup(event.x_root, event.y_root)

    def _on_select(self, _event) -> None:
        sel = self.tree.selection()
        if not sel or sel[0] not in self.iid_map:
            return
        path = self.iid_map[sel[0]][1]
        try:
            from PIL import Image, ImageTk
            with Image.open(path) as im:
                im.thumbnail((300, 300))
                self.preview_ref = ImageTk.PhotoImage(im.copy())
            self.preview.config(image=self.preview_ref, text="")
        except Exception:  # noqa: BLE001 - not an image / Pillow missing: show text instead
            self.preview_ref = None
            self.preview.config(image="", text=f"{path.name}\n{human(path.stat().st_size) if path.exists() else ''}")

    # ── clean ──────────────────────────────────────────────────────────────────
    def clean(self) -> None:
        n, size, similar = summary(self.groups)
        if not n:
            return
        action = self.vars["action"].get()
        dest = "a Lixeira" if action == "trash" else f"a pasta de quarentena\n{self.quarantine.get()}"
        warn = (f"\n\nAtenção: {similar} arquivo(s) vêm de grupos “parecidos” (não idênticos). "
                "Confira antes.") if similar else ""
        if not messagebox.askyesno(
                "Confirmar limpeza",
                f"Mover {n} arquivo(s) ({human(size)}) para {dest}?\n\nNada será apagado de vez; "
                f"pelo menos uma cópia de cada grupo continua no lugar.{warn}"):
            return
        try:
            out = apply_plans(to_plans(self.groups), action=action, roots=list(self.paths),
                              quarantine_dir=Path(self.quarantine.get()), apply=True)
        except RuntimeError as exc:
            messagebox.showerror("TwinFinder", str(exc))
            return
        msg = (f"Movidos: {out.count('moved') + out.count('trashed')}\n"
               f"Pulados: {out.count('skipped')}\nErros: {out.count('error')}")
        if action == "quarantine":
            msg += f"\n\nOs arquivos estão em:\n{self.quarantine.get()}\nApague essa pasta quando tiver certeza."
        messagebox.showinfo("Limpeza concluída", msg)
        self.groups = []
        self._render()
        self.status.config(text="Limpeza concluída. Faça uma nova busca se quiser conferir.")


def run() -> None:
    App().mainloop()
