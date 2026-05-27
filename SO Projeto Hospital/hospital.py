# main.py - Este código permite dar run ao programa
from multiprocessing import Queue, Process,Manager
import tkinter as tk
from interface import InterfaceHospital
from hospital_core import gerar_pacientes, atender_pacientes

if __name__ == "__main__":
    manager = Manager()
    estado_pacientes = manager.dict()

    fila_pacientes = Queue()
    fila_visual = Queue()

    p1 = Process(target=gerar_pacientes, args=(fila_pacientes, fila_visual, estado_pacientes))
    p2 = Process(target=atender_pacientes, args=(fila_pacientes, fila_visual, estado_pacientes))

    p1.start()
    p2.start()

    root = tk.Tk()
    app = InterfaceHospital(root, fila_visual)

    def actualizar_interface():
        app.actualizar(fila_visual)
        root.after(1000, actualizar_interface)

    root.after(1000, actualizar_interface)
    root.mainloop()
