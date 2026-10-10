import queue
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Optional

from .takeout import ApplyOptions, TakeoutScan, apply_takeout, scan_takeout

class TakeoutWindow(tk.Toplevel):
    def __init__(self, parent: tk.Tk) -> None:
        super().__init__(parent)
        self.title("Restaurar Google Photos Takeout")
        self.geometry("700x550")
        self.minsize(600, 500)
        
        self.paths: list[Path] = []
        self.scan_res: Optional[TakeoutScan] = None
        
        self.vars = {
            "set_dates": tk.BooleanVar(value=True),
            "exif": tk.BooleanVar(value=True),
            "move_sidecars": tk.StringVar(value=""),
            "overwrite_exif": tk.BooleanVar(value=False),
        }
        
        self._build()

    def _build(self) -> None:
        pad = {"padx": 8, "pady": 4}
        
        top = ttk.LabelFrame(self, text="1. Pastas do Takeout para analisar")
        top.pack(fill="x", **pad)
        self.lb = tk.Listbox(top, height=3)
        self.lb.pack(side="left", fill="x", expand=True, padx=6, pady=6)
        col = ttk.Frame(top)
        col.pack(side="left", padx=6)
        ttk.Button(col, text="Adicionar pasta…", command=self.add_folder).pack(fill="x", pady=2)
        ttk.Button(col, text="Remover", command=self.remove_folder).pack(fill="x", pady=2)
        
        opt = ttk.LabelFrame(self, text="2. Opções de Restauração")
        opt.pack(fill="x", **pad)
        ttk.Checkbutton(opt, text="Corrigir a Data de Modificação do arquivo", variable=self.vars["set_dates"]).pack(anchor="w", padx=6, pady=2)
        ttk.Checkbutton(opt, text="Gravar Data/GPS DENTRO do arquivo EXIF (Requer ExifTool)", variable=self.vars["exif"]).pack(anchor="w", padx=6, pady=2)
        ttk.Checkbutton(opt, text="Sobrescrever datas existentes no EXIF (padrão: apenas preencher se estiver vazio)", variable=self.vars["overwrite_exif"]).pack(anchor="w", padx=6, pady=2)
        
        mov = ttk.Frame(opt)
        mov.pack(fill="x", padx=6, pady=2)
        ttk.Label(mov, text="Mover os .json lidos para (vazio = não mover):").pack(side="left")
        ttk.Entry(mov, textvariable=self.vars["move_sidecars"], width=30).pack(side="left", padx=6)
        ttk.Button(mov, text="Escolher…", command=self.pick_dest).pack(side="left")

        mid = ttk.Frame(self)
        mid.pack(fill="both", expand=True, **pad)
        self.btn_scan = ttk.Button(mid, text="Analisar Pastas", command=self.scan)
        self.btn_scan.pack(pady=4)
        
        self.status = ttk.Label(mid, text="Adicione as pastas e clique em Analisar.", justify="center")
        self.status.pack(pady=8)
        
        bot = ttk.Frame(self)
        bot.pack(fill="x", **pad)
        self.btn_apply = ttk.Button(bot, text="Aplicar Correções (Sem Volta)", command=self.apply_fixes, state="disabled")
        self.btn_apply.pack(pady=10)

    def add_folder(self) -> None:
        d = filedialog.askdirectory(title="Escolha a pasta do Takeout")
        if d and Path(d) not in self.paths:
            self.paths.append(Path(d))
            self.lb.insert("end", d)
            self.btn_apply.config(state="disabled")

    def remove_folder(self) -> None:
        for i in reversed(self.lb.curselection()):
            self.lb.delete(i)
            del self.paths[i]
            self.btn_apply.config(state="disabled")

    def pick_dest(self) -> None:
        d = filedialog.askdirectory(title="Pasta para os arquivos .json")
        if d:
            self.vars["move_sidecars"].set(d)

    def scan(self) -> None:
        if not self.paths:
            return messagebox.showinfo("Aviso", "Adicione pastas.")
        self.btn_scan.config(state="disabled", text="Analisando...")
        self.update()
        
        try:
            self.scan_res = scan_takeout(self.paths)
            msg = (f"Encontrados:\n\n"
                   f"✅ {len(self.scan_res.pairs)} fotos/vídeos com o .json correspondente.\n"
                   f"⚠️ {len(self.scan_res.media_without_sidecar)} fotos sem .json (não serão alteradas).\n"
                   f"🗑️ {len(self.scan_res.orphan_sidecars)} arquivos .json órfãos (sem foto).")
            self.status.config(text=msg)
            if self.scan_res.pairs:
                self.btn_apply.config(state="normal")
        except Exception as e:
            messagebox.showerror("Erro", str(e))
        finally:
            self.btn_scan.config(state="normal", text="Analisar Pastas")

    def apply_fixes(self) -> None:
        if not self.scan_res or not self.scan_res.pairs:
            return
        if not messagebox.askyesno("Confirmar", f"Isso irá modificar {len(self.scan_res.pairs)} arquivos. Tem certeza?"):
            return
            
        move_p = self.vars["move_sidecars"].get()
        opts = ApplyOptions(
            set_dates=self.vars["set_dates"].get(),
            exif=self.vars["exif"].get(),
            only_missing=not self.vars["overwrite_exif"].get(),
            move_sidecars_to=Path(move_p) if move_p else None,
            keep_exiftool_backup=False
        )
        
        self.btn_apply.config(state="disabled", text="Aplicando...")
        self.update()
        
        def work():
            try:
                rep = apply_takeout(self.scan_res, opts, apply=True)
                msg = f"Sucesso!\nDatas alteradas: {rep.dates_set}\nEXIF gravados: {rep.exif_written}\nJSONs movidos: {rep.sidecars_moved}"
                if rep.errors:
                    msg += f"\n\nOcorreram {len(rep.errors)} erros (veja terminal)."
                messagebox.showinfo("Concluído", msg)
            except Exception as e:
                messagebox.showerror("Erro", str(e))
            finally:
                self.btn_apply.config(state="normal", text="Aplicar Correções (Sem Volta)")
                self.scan_res = None
                self.status.config(text="Limpeza concluída. Analise novamente se desejar.")
                
        threading.Thread(target=work, daemon=True).start()
