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
        self.title("Google Photos Takeout Fixer")
        self.geometry("750x750")
        self.minsize(650, 650)
        self.transient(parent) # Makes it float over the parent
        self.grab_set()        # Modal window
        
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
        # Estilo para os frames
        style = ttk.Style(self)
        style.configure("TFrame", background=self.cget("bg"))
        
        main_frame = ttk.Frame(self, padding="20 20 20 20")
        main_frame.pack(fill="both", expand=True)
        
        # Header
        hdr = ttk.Label(main_frame, text="Restauração de Metadados (Google Takeout)", font=("Segoe UI", 16, "bold"))
        hdr.pack(anchor="w", pady=(0, 5))
        sub_hdr = ttk.Label(main_frame, text="Reconecta os arquivos .json às suas respectivas fotos/vídeos para recuperar Data e GPS.", foreground="#555")
        sub_hdr.pack(anchor="w", pady=(0, 20))

        # 1. Pastas
        top = ttk.LabelFrame(main_frame, text=" 1. Pastas da Exportação ", padding="10 10 10 10")
        top.pack(fill="x", pady=(0, 15))
        
        self.lb = tk.Listbox(top, height=4, relief="flat", borderwidth=1, highlightthickness=1, highlightcolor="#ccc")
        self.lb.pack(side="left", fill="x", expand=True, padx=(0, 10))
        
        col = ttk.Frame(top)
        col.pack(side="left")
        ttk.Button(col, text="Adicionar pasta…", command=self.add_folder, width=20).pack(fill="x", pady=(0, 5))
        ttk.Button(col, text="Remover", command=self.remove_folder, width=20).pack(fill="x")
        
        # 2. Opções
        opt = ttk.LabelFrame(main_frame, text=" 2. Como Restaurar? ", padding="10 10 10 10")
        opt.pack(fill="x", pady=(0, 20))
        
        ttk.Checkbutton(opt, text="Corrigir a Data de Criação/Modificação do arquivo no Windows", variable=self.vars["set_dates"]).grid(row=0, column=0, sticky="w", pady=4)
        ttk.Checkbutton(opt, text="Gravar Data/GPS DENTRO do EXIF do arquivo (Requer ExifTool)", variable=self.vars["exif"]).grid(row=1, column=0, sticky="w", pady=4)
        ttk.Checkbutton(opt, text="Sobrescrever datas EXIF (Desmarque para preencher apenas os vazios)", variable=self.vars["overwrite_exif"]).grid(row=2, column=0, sticky="w", pady=4)
        
        sep = ttk.Separator(opt, orient="horizontal")
        sep.grid(row=3, column=0, sticky="we", pady=10)
        
        mov = ttk.Frame(opt)
        mov.grid(row=4, column=0, sticky="we")
        ttk.Label(mov, text="Mover os .json processados para:").pack(side="left", padx=(0, 5))
        ttk.Entry(mov, textvariable=self.vars["move_sidecars"], width=35).pack(side="left", padx=5)
        ttk.Button(mov, text="Procurar…", command=self.pick_dest).pack(side="left")

        # 3. Análise
        mid = ttk.Frame(main_frame)
        mid.pack(fill="both", expand=True)
        
        self.btn_scan = ttk.Button(mid, text="1º Passo: Analisar Pastas", command=self.scan, style="Accent.TButton")
        self.btn_scan.pack(pady=(10, 5))
        
        self.status = ttk.Label(mid, text="Adicione as pastas e clique em Analisar para começar.", justify="center", font=("Segoe UI", 10))
        self.status.pack(pady=10)
        
        # 4. Ação Final
        self.btn_apply = ttk.Button(mid, text="2º Passo: Aplicar Correções (Irreversível)", command=self.apply_fixes, state="disabled")
        self.btn_apply.pack(pady=(15, 0))
        
        self.bar_apply = ttk.Progressbar(mid, mode="determinate", length=400)
        self.lbl_eta = ttk.Label(mid, text="", font=("Segoe UI", 9))
        self.bar_apply.pack(pady=(15, 5))
        self.lbl_eta.pack()
        self.bar_apply.pack_forget()
        self.lbl_eta.pack_forget()

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
        d = filedialog.askdirectory(title="Mover JSONs para...")
        if d:
            self.vars["move_sidecars"].set(d)

    def scan(self) -> None:
        if not self.paths:
            return messagebox.showinfo("Aviso", "Adicione pastas primeiro.")
        self.btn_scan.config(state="disabled", text="Analisando...")
        self.update()
        
        try:
            self.scan_res = scan_takeout(self.paths)
            msg = (f"Resultados da Análise:\n\n"
                   f"🟢 {len(self.scan_res.pairs)} fotos/vídeos com o .json correspondente encontrados!\n"
                   f"🟠 {len(self.scan_res.media_without_sidecar)} mídias sem .json (não serão afetadas).\n"
                   f"⚪ {len(self.scan_res.orphan_sidecars)} arquivos .json órfãos (sem foto correspondente).")
            self.status.config(text=msg)
            if self.scan_res.pairs:
                self.btn_apply.config(state="normal")
        except Exception as e:
            messagebox.showerror("Erro", str(e))
        finally:
            self.btn_scan.config(state="normal", text="1º Passo: Analisar Pastas")

    def apply_fixes(self) -> None:
        if not self.scan_res or not self.scan_res.pairs:
            return
        if not messagebox.askyesno("Confirmar", f"Isso irá injetar os metadados em {len(self.scan_res.pairs)} arquivos.\nVocê tem certeza?"):
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
        
        self.bar_apply.pack(pady=(15, 5))
        self.lbl_eta.pack()
        self.bar_apply["value"] = 0
        self.bar_apply["maximum"] = 100
        self.lbl_eta.config(text="Iniciando...")
        
        import time
        start_time = time.time()
        
        def cb_progress(current, total):
            pct = int((current / total) * 100) if total else 100
            elapsed = time.time() - start_time
            if current > 0 and elapsed > 0:
                speed = current / elapsed
                rem_sec = (total - current) / speed
                mins, secs = divmod(int(rem_sec), 60)
                eta_str = f"Restante: {mins}m {secs}s"
            else:
                eta_str = "Calculando..."
            
            self.after(0, lambda: _update_ui(pct, eta_str, current, total))

        def _update_ui(pct, eta_str, current, total):
            self.bar_apply["value"] = pct
            self.lbl_eta.config(text=f"Processando: {pct}% ({current}/{total}) | {eta_str}")

        def work():
            try:
                rep = apply_takeout(self.scan_res, opts, apply=True, progress=cb_progress)
                msg = f"Sucesso!\n\n📅 Datas de arquivo alteradas: {rep.dates_set}\n📸 Tags EXIF gravadas: {rep.exif_written}\n📦 Arquivos JSON movidos: {rep.sidecars_moved}"
                if rep.errors:
                    msg += f"\n\nOcorreram {len(rep.errors)} erros (veja o terminal para mais detalhes)."
                messagebox.showinfo("Concluído", msg)
            except Exception as e:
                messagebox.showerror("Erro", str(e))
            finally:
                self.after(0, lambda: self.bar_apply.pack_forget())
                self.after(0, lambda: self.lbl_eta.pack_forget())
                self.btn_apply.config(state="normal", text="2º Passo: Aplicar Correções (Irreversível)")
                self.scan_res = None
                self.status.config(text="Limpeza concluída. Analise novamente se desejar processar mais fotos.")
                
        threading.Thread(target=work, daemon=True).start()
