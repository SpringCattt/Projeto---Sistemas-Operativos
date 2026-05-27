# interface.py - Este código trata da interface do programa
import tkinter as tk
from queue import Empty

class InterfaceHospital:
    def __init__(self, root, queue_visual):
        self.root = root
        self.queue_visual = queue_visual
        self.root.title("Simulador de Hospital")
        self.root.geometry("900x550")
        self.root.resizable(False, False)  # Bloqueia redimensionamento e maximizar janela

        # Frame principal superior (pacientes na fila e em atendimento)
        frame_principal = tk.Frame(root, bg="#f0f4f8")
        frame_principal.pack(fill=tk.BOTH, expand=False, padx=10, pady=10)

        # Frame para lista de pacientes esperando na fila
        frame_fila = tk.Frame(frame_principal, bg="#dce9f9", bd=2, relief="groove")
        frame_fila.pack(side=tk.LEFT, padx=10, pady=5, fill=tk.BOTH, expand=True)
        tk.Label(frame_fila, text="Pacientes na Fila", font=("Helvetica", 14, "bold"), bg="#dce9f9", fg="#2c3e50").pack(pady=(10,5))
        self.lista_espera = tk.Listbox(frame_fila, bg="white", font=("Consolas", 11), width=40,bd=1, highlightbackground="black", highlightthickness=1)                             
        self.lista_espera.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0,10))

        # Frame para lista de pacientes em atendimento
        frame_atendiendo = tk.Frame(frame_principal, bg="#dce9f9", bd=2, relief="groove")
        frame_atendiendo.pack(side=tk.LEFT, padx=10, pady=5, fill=tk.BOTH, expand=True)
        tk.Label(frame_atendiendo, text="Pacientes em Atendimento", font=("Helvetica", 14, "bold"), bg="#dce9f9", fg="#2c3e50").pack(pady=(10,5))
        self.lista_atendiendo = tk.Listbox(frame_atendiendo, bg="white", font=("Consolas", 11), width=40,bd=1, highlightbackground="black", highlightthickness=1)                                         
        self.lista_atendiendo.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0,10))



        # Frame para o log de eventos, com borda e estilo
        frame_log = tk.Frame(root, bd=2, relief="groove", bg="#e6f0fa")
        frame_log.pack(side=tk.BOTTOM, fill=tk.BOTH, expand=True, padx=10, pady=10)

        # Label do log centralizado
        tk.Label(frame_log, text="Log de Eventos", font=("Helvetica", 14, "bold"), bg="#e6f0fa", fg="#2c3e50").pack(side=tk.TOP, pady=(10,5), fill=tk.X)

        frame_log_interno = tk.Frame(frame_log, bg="#e6f0fa")
        frame_log_interno.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0,10))

        # Widget Text para mostrar log, com scrollbar
        self.texto_log = tk.Text(frame_log_interno, height=10, bg="white", font=("Consolas", 11), wrap="word", bd=1, relief="solid")
        self.texto_log.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        scrollbar_log = tk.Scrollbar(frame_log_interno, command=self.texto_log.yview)
        scrollbar_log.pack(side=tk.RIGHT, fill=tk.Y)
        self.texto_log.config(yscrollcommand=scrollbar_log.set)

        # Configura tags para cores específicas e estilo de texto do SURTO e desistiu
        self.texto_log.tag_configure("verm", foreground="red")  # Vermelha em vermelho
        self.texto_log.tag_configure("amel", foreground="orange")  # Amarela em laranja
        self.texto_log.tag_configure("verd", foreground="green")  # Verde em verde
        self.texto_log.tag_configure("surto", foreground="red", font=("Helvetica", 14, "bold"), justify="center")  # SURTO em vermelho, grande e centralizado
        self.texto_log.tag_configure("center", justify="center")
        self.texto_log.tag_configure("desistiu", foreground="red")  # desistiu em vermelho

        # Dicionários para controlar pacientes na fila e em atendimento
        self.pacientes_esperando = {}
        self.pacientes_atendiendo = {}

        # Abre o arquivo de log em modo append para salvar histórico
        self.archivo_log = open("hospital.log", "w", encoding="utf-8")

        # Inicia a atualização da interface (loop de atualização)
        self.actualizar()

    def log(self, mensagem):
        # Verifica se o scroll está próximo do final (para saber se dar scroll automaticamente)
        yview = self.texto_log.yview()
        esta_abajo = yview[1] >= 0.99  # Se estiver praticamente no fim

        start_index = self.texto_log.index("end-1c")
        if mensagem == ">>> SURTO DE PACIENTES! <<<":
            # Insere linha centralizada e com tag especial para SURTO
            self.texto_log.insert(tk.END, mensagem + "\n", ("surto", "center"))
        else:
            # Insere a mensagem normalmente
            self.texto_log.insert(tk.END, mensagem + "\n")

            # Aplica tag de cor para "desistiu de esperar" especificamente
            if "desistiu de esperar" in mensagem:
                idx = start_index
                while True:
                    idx = self.texto_log.search("desistiu de esperar", idx, stopindex="end")
                    if not idx:
                        break
                    end_idx = f"{idx}+{len('desistiu de esperar')}c"
                    self.texto_log.tag_add("desistiu", idx, end_idx)
                    idx = end_idx

            # Aplica tags de cor para as urgências
            for palavra, tag in [("Vermelha", "verm"), ("Amarela", "amel"), ("Verde", "verd")]:
                idx = start_index
                while True:
                    idx = self.texto_log.search(palavra, idx, stopindex="end")
                    if not idx:
                        break
                    end_idx = f"{idx}+{len(palavra)}c"
                    self.texto_log.tag_add(tag, idx, end_idx)
                    idx = end_idx

        # Se estava no fim, faz scroll para o fim; se não, mantem a posicao do usuário
        if esta_abajo:
            self.texto_log.see(tk.END)

        # Escreve a mensagem no arquivo de log
        self.archivo_log.write(mensagem + "\n")
        self.archivo_log.flush()

    def actualizar(self):
        # Processa todos os eventos da fila
        try:
            while True:
                evento = self.queue_visual.get_nowait()
                if evento[0] == 'esperando':
                    _, id, nome, urg, hora = evento
                    texto = f"[{hora}] [{id}] {nome} ({urg})"
                    self.pacientes_esperando[id] = texto
                    self.log(f"[{hora}] [{id}] {nome} entrou na fila com urgência {urg}.")
                elif evento[0] == 'atendiendo':
                    _, id, nome, urg, hora = evento
                    self.pacientes_esperando.pop(id, None)
                    self.pacientes_atendiendo[id] = f"[{hora}] [{id}] {nome} ({urg})"
                    self.log(f"[{hora}] [{id}] {nome} começou o atendimento com urgência {urg}.")
                elif evento[0] == 'atendido':
                    _, id, nome, urg, hora = evento
                    self.pacientes_atendiendo.pop(id, None)
                    self.log(f"[{hora}] [{id}] {nome} foi atendido e saiu.")
                elif evento[0] == 'desistiu':
                    _, id, nome, urg, hora = evento
                    self.pacientes_esperando.pop(id, None)
                    self.log(f"[{hora}] [{id}] {nome} desistiu de esperar.")
                elif evento[0] == 'SURTO':
                    _, hora = evento
                    self.log(f">>> SURTO DE PACIENTES! <<<")
        except Empty:
            pass

        # Atualiza as listas visuais de pacientes esperando e em atendimento
        self.lista_espera.delete(0, tk.END)
        for p in self.pacientes_esperando.values():
            # Aplica cor conforme urgência na lista de espera
            if "Vermelha" in p:
                self.lista_espera.insert(tk.END, p)
                self.lista_espera.itemconfig(tk.END, fg="red")
            elif "Amarela" in p:
                self.lista_espera.insert(tk.END, p)
                self.lista_espera.itemconfig(tk.END, fg="orange")
            elif "Verde" in p:
                self.lista_espera.insert(tk.END, p)
                self.lista_espera.itemconfig(tk.END, fg="green")
            else:
                self.lista_espera.insert(tk.END, p)

        self.lista_atendiendo.delete(0, tk.END)
        for p in self.pacientes_atendiendo.values():
            # Aplica cor conforme urgência na lista de atendimento
            if "Vermelha" in p:
                self.lista_atendiendo.insert(tk.END, p)
                self.lista_atendiendo.itemconfig(tk.END, fg="red")
            elif "Amarela" in p:
                self.lista_atendiendo.insert(tk.END, p)
                self.lista_atendiendo.itemconfig(tk.END, fg="orange")
            elif "Verde" in p:
                self.lista_atendiendo.insert(tk.END, p)
                self.lista_atendiendo.itemconfig(tk.END, fg="green")
            else:
                self.lista_atendiendo.insert(tk.END, p)

        # Chama essa função novamente após 1 segundo para atualizar interface
        self.root.after(1000, self.actualizar)

    def __del__(self):
        # Fecha o arquivo de log ao fechar a interface
        if hasattr(self, 'archivo_log'):
            self.archivo_log.close()
