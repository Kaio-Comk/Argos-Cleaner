import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from pathlib import Path
import threading
import os

def check_magic(path: Path) -> str | None:
    try:
        with open(path, 'rb') as f:
            head = f.read(12)
    except OSError:
        return None
    if head.startswith(b'\xff\xd8\xff'):
        return '.jpg'
    if head.startswith(b'\x89PNG\r\n\x1a\n'):
        return '.png'
    if head.startswith(b'RIFF') and len(head) >= 12 and head[8:12] == b'WEBP':
        return '.webp'
    if len(head) >= 12 and head[4:8] == b'ftyp':
        brand = head[8:12]
        if brand in (b'heic', b'heix', b'hevc', b'mif1'):
            return '.heic'
        return '.mp4'
    return None

class FixerWindow(tk.Toplevel):
    def __init__(self, parent: tk.Tk) -> None:
        super().__init__(parent)
        self.title("Corrigir Extensões (Arquivos Mágicos)")
        self.geometry("600x300")
        self.transient(parent)
        self.grab_set()

        self.target_dir = tk.StringVar()

        frame = ttk.Frame(self, padding="20 20 20 20")
        frame.pack(fill="both", expand=True)

        ttk.Label(frame, text="Reparador de Extensões", font=("Segoe UI", 16, "bold")).pack(anchor="w")
        ttk.Label(frame, text="Analisa os arquivos de uma pasta e corrige a extensão\ncaso ela não corresponda ao conteúdo real da imagem/vídeo.\nÚtil para erros do Google Takeout (ex: PNG salvo como JPG).").pack(anchor="w", pady=(5, 20))

        # Pasta
        row = ttk.Frame(frame)
        row.pack(fill="x", pady=5)
        ttk.Label(row, text="Pasta alvo:").pack(side="left")
        ttk.Entry(row, textvariable=self.target_dir, state="readonly").pack(side="left", fill="x", expand=True, padx=5)
        ttk.Button(row, text="Procurar...", command=self._browse).pack(side="left")

        # Botão
        self.btn_run = ttk.Button(frame, text="Analisar e Corrigir", style="Accent.TButton", command=self._run)
        self.btn_run.pack(pady=20)

        # Status
        self.status = ttk.Label(frame, text="")
        self.status.pack()

    def _browse(self):
        d = filedialog.askdirectory(title="Selecione a pasta", parent=self)
        if d:
            self.target_dir.set(d)

    def _run(self):
        d = self.target_dir.get()
        if not d:
            return
        
        self.btn_run.config(state="disabled")
        self.status.config(text="Analisando arquivos...")

        def work():
            fixed = 0
            errors = 0
            try:
                for root, _, files in os.walk(d):
                    for f in files:
                        path = Path(root) / f
                        if path.is_symlink() or not path.is_file():
                            continue
                        
                        ext = check_magic(path)
                        if ext and path.suffix.lower() != ext and not (ext == ".jpg" and path.suffix.lower() == ".jpeg"):
                            new_path = path.with_suffix(ext)
                            # Se já existir um com a extensão correta, adicionar número
                            n = 1
                            while new_path.exists():
                                new_path = path.with_name(f"{path.stem}_{n}{ext}")
                                n += 1
                            try:
                                path.rename(new_path)
                                fixed += 1
                            except OSError:
                                errors += 1
            except Exception as e:
                self.after(0, lambda e=e: messagebox.showerror("Erro", str(e), parent=self))
            
            self.after(0, self._done, fixed, errors)

        threading.Thread(target=work, daemon=True).start()

    def _done(self, fixed, errors):
        self.btn_run.config(state="normal")
        self.status.config(text="")
        msg = f"Processo concluído!\n\nArquivos corrigidos: {fixed}"
        if errors:
            msg += f"\nErros ao renomear: {errors}"
        messagebox.showinfo("Sucesso", msg, parent=self)


