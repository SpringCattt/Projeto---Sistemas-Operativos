# hospital_core.py - Este código trata de toda a parte lógica do programa
import random
import time
from multiprocessing import Process
from datetime import datetime

# Listas de nomes e apelidos usados para gerar pacientes aleatórios
nomes = ['Ana<3', 'Carlos', 'Mariana', 'José', 'Paula', 'Miguel', 'Laura', 'Tiago', 'Sofia', 'Rui',
         'Pedro', 'Rafael', 'Thomaz', 'Felipe', 'Manuel', 'Mario', 'Rosa', 'Beatriz', 'Diana']
apelidos = ['Silva', 'Santos', 'Ferreira', 'Pereira', 'Costa', 'Oliveira', 'Martins', 'Rocha',
            'Almeida', 'Ramos', 'Vasco', 'Araujo', 'Liendo', 'Cambin', 'Fitorra']

# Dicionário que associa cada cor de urgência a uma prioridade (quanto menor, mais urgente)
urgencias = {'Verde': 2, 'Amarela': 1, 'Vermelha': 0}

# Número máximo de médicos disponíveis ao mesmo tempo
MAX_MEDICOS = 5

# Gera um nome aleatório com o nome e sobrenome
def gerar_nome_completo():
    return f"{random.choice(nomes)} {random.choice(apelidos)}"

# Escolhe uma urgência aleatória com pesos diferentes (60% verde, 30% amarelo e 10% vermelho)
def gerar_urgencia():
    return random.choices(list(urgencias.keys()), weights=[0.6, 0.3, 0.1])[0]

# Retorna a hora atual formatada
def hora_atual():
    return datetime.now().strftime('%H:%M:%S')

# Processo individual que representa a vida de um paciente
def paciente_processo(id, fila_pacientes, fila_visual, estado_pacientes):
    nome = gerar_nome_completo()
    urg = gerar_urgencia()
    prioridade = urgencias[urg]
    tempo_max_espera = random.randint(15, 20)  # tempo que o paciente pode esperar antes de desistir, em segundos
    tempo_inicial = time.time()

    estado_pacientes[id] = 'esperando'
    fila_pacientes.put((prioridade, id, nome, urg, tempo_inicial, tempo_max_espera))
    fila_visual.put(('esperando', id, nome, urg, hora_atual()))

    # Espera até ser atendido ou desiste
    while time.time() - tempo_inicial < tempo_max_espera:
        if estado_pacientes.get(id) != 'esperando':
            return
        time.sleep(1)

    # Se ainda não foi atendido, desiste
    if estado_pacientes.get(id) == 'esperando':
        estado_pacientes[id] = 'desistiu'
        fila_visual.put(('desistiu', id, nome, urg, hora_atual()))

# Função que simula o atendimento de um paciente
def atendimento(paciente, fila_visual, estado_pacientes):
    prioridade, id, nome, urg, tempo_inicial, tempo_max_espera = paciente
    estado_pacientes[id] = 'atendendo'
    fila_visual.put(('atendiendo', id, nome, urg, hora_atual()))
    time.sleep(random.randint(5, 10))  # Tempo de atendimento
    estado_pacientes[id] = 'atendido'
    fila_visual.put(('atendido', id, nome, urg, hora_atual()))

# Processo principal responsável por gerenciar os atendimentos
def atender_pacientes(fila_pacientes, fila_visual, estado_pacientes):
    processos = []  # Armazena processos de atendimento ativos

    while True:
        # Remove processos que já terminaram (livera medicos)
        processos = [p for p in processos if p.is_alive()]
        medicos_livres = MAX_MEDICOS - len(processos) #calcula medicos livres

        if medicos_livres > 0:
            pacientes_disponiveis = [] 
            while not fila_pacientes.empty(): #tira os pacientes e os analiza 1 por 1
                paciente = fila_pacientes.get() 
                tempo_inicial = paciente[4]
                tempo_max_espera = paciente[5]
                id = paciente[1]
                if time.time() - tempo_inicial < tempo_max_espera and estado_pacientes.get(id) == 'esperando':
                    pacientes_disponiveis.append(paciente)

            if pacientes_disponiveis:
                pacientes_disponiveis.sort()  # Menor prioridade (mais urgente) vem primeiro
                para_atender = pacientes_disponiveis[:medicos_livres]
                restante = pacientes_disponiveis[medicos_livres:]

                for p in restante:
                    if estado_pacientes.get(p[1]) == 'esperando':
                        fila_pacientes.put(p)

                for paciente in para_atender:
                    p = Process(target=atendimento, args=(paciente, fila_visual, estado_pacientes))
                    p.start()
                    processos.append(p)

        time.sleep(1)

# Processo responsável por gerar pacientes aleatórios
def gerar_pacientes(fila_pacientes, fila_visual, estado_pacientes):
    id_paciente = 1
    while True:
        # Conta quantos pacientes estão atualmente esperando
        total_esperando = sum(1 for estado in estado_pacientes.values() if estado == 'esperando')
        if total_esperando >= 20:
            time.sleep(1)
            continue 

        if random.random() < 0.05:  # 5% de chance de "surto" onde são gerados mais pacientes
            inicio_surto = time.time()
            fila_visual.put(("SURTO", hora_atual()))
            while time.time() - inicio_surto < 10:
                for _ in range(random.randint(3, 5)):
                    if sum(1 for estado in estado_pacientes.values() if estado == 'esperando') >= 20:
                        break # se já ha 20 pacientes, não cria mais
                    p = Process(target=paciente_processo, args=(id_paciente, fila_pacientes, fila_visual, estado_pacientes))
                    p.start()
                    id_paciente += 1
                time.sleep(random.uniform(1, 4))
        else:
            for _ in range(random.randint(1, 3)):
                if sum(1 for estado in estado_pacientes.values() if estado == 'esperando') >= 20:
                    break # se já ha 20 pacientes, não cria mais
                p = Process(target=paciente_processo, args=(id_paciente, fila_pacientes, fila_visual, estado_pacientes))
                p.start()
                id_paciente += 1
            time.sleep(random.uniform(1, 4))