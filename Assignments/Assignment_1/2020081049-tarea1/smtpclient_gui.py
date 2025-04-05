import argparse  # Para el manejo de argumentos de línea de comandos
import csv       # Para la lectura de archivos CSV
import os        # Para interacciones con el sistema operativo
import sys       # Para acceso a funcionalidades del sistema, como la salida estándar
import tkinter as tk  # Para la creación de interfaces gráficas
from tkinter import filedialog, messagebox  # Para diálogos de selección de archivo y mensajes

from email.mime.text import MIMEText         # Para crear mensajes de correo en formato de texto
from email.mime.multipart import MIMEMultipart   # Para crear mensajes con múltiples partes (texto y adjuntos)
from email.mime.base import MIMEBase           # Para crear la parte base de un adjunto
from email import encoders                     # Para codificar adjuntos

from twisted.internet import reactor, defer, tksupport  # Para la integración asíncrona con Twisted y Tkinter
from twisted.mail.smtp import sendmail         # Para enviar correos electrónicos usando SMTP a través de Twisted
from twisted.python import log                   # Para registrar logs en consola



def send_email(row, host, port, message_template, attachment_file=None):
    """
    Envía un correo electrónico utilizando los datos proporcionados en 'row'.
    
    Parámetros:
    row -- Lista con los datos [nombre, emisor, receptor, asunto]
    host -- Servidor SMTP
    port -- Puerto del servidor SMTP
    message_template -- Plantilla de mensaje que puede incluir un marcador {nombre}
    attachment_file -- (Opcional) Ruta al archivo adjunto
    
    Retorna:
    Un Deferred que representa el envío del mensaje mediante sendmail de Twisted.
    """
    nombre, sender, recipient, subject = row[0], row[1], row[2], row[3]

    # Personalizar el mensaje reemplazando el marcador {nombre}
    personalized_message = message_template.format(nombre=nombre)

    # Imprimir información del correo que se enviará (útil para depuración)
    print(f"Enviando correo a {recipient} desde {sender} con asunto '{subject}'")
    print("Contenido del mensaje:")
    print(personalized_message)
    if attachment_file:
        print(f"Adjuntando archivo: {attachment_file}")
    print("-" * 40)

    # Preparar el mensaje, considerando si hay un archivo adjunto o no
    if attachment_file:
        # Crear un mensaje multipart si hay adjunto
        msg = MIMEMultipart()
        msg["Subject"] = subject
        msg["From"] = sender
        msg["To"] = recipient

        # Adjuntar el cuerpo del mensaje en formato de texto plano
        body = MIMEText(personalized_message, "plain", "utf-8")
        msg.attach(body)

        # Verificar que el archivo adjunto existe y adjuntarlo al mensaje
        if os.path.exists(attachment_file):
            with open(attachment_file, "rb") as attachment:
                part = MIMEBase("application", "octet-stream")
                part.set_payload(attachment.read())
            encoders.encode_base64(part)
            filename = os.path.basename(attachment_file)
            part.add_header("Content-Disposition", f'attachment; filename="{filename}"')
            msg.attach(part)
        else:
            print(f"⚠ El archivo de adjunto '{attachment_file}' no existe. Se enviará sin adjunto.")
    else:
        # Crear un mensaje simple de texto si no hay adjunto
        msg = MIMEText(personalized_message, "plain", "utf-8")
        msg["Subject"] = subject
        msg["From"] = sender
        msg["To"] = recipient

    # Enviar el mensaje utilizando sendmail de Twisted.
    # Se codifica el mensaje en UTF-8.
    return sendmail(host, sender, [recipient], msg.as_string().encode('utf-8'), port=port)

# Función que se invoca al presionar el botón "Enviar Correos" en la interfaz gráfica.
def send_emails_callback():
    """
    Callback para el botón de envío de correos.
    Obtiene los datos de la interfaz, valida los archivos, 
    lee la plantilla y el CSV, y envía los correos electrónicos de forma asíncrona.
    """
    host = entry_host.get().strip()
    try:
        port = int(entry_port.get().strip())
    except ValueError:
        messagebox.showerror("Error", "El puerto debe ser un número entero.")
        return
    csv_path = entry_csv.get().strip()
    msg_path = entry_msg.get().strip()
    attachment = entry_attachment.get().strip() or None

    # Validar que los archivos CSV y de mensaje existen
    if not os.path.exists(csv_path):
        messagebox.showerror("Error", f"No se encontró el archivo CSV:\n{csv_path}")
        return
    if not os.path.exists(msg_path):
        messagebox.showerror("Error", f"No se encontró el archivo de mensaje:\n{msg_path}")
        return

    try:
        # Leer la plantilla del mensaje
        with open(msg_path, 'r', encoding='utf-8') as f:
            message_template = f.read()

        # Leer el CSV y almacenar las filas
        with open(csv_path, 'r', encoding='utf-8') as csvfile:
            reader = csv.reader(csvfile)
            rows = list(reader)
            # Si la primera fila es una cabecera, se omite
            if rows and rows[0][0].lower() == "nombre":
                print("Se detectó cabecera en el CSV, omitiendo la primera fila.")
                rows = rows[1:]
    except Exception as e:
        messagebox.showerror("Error", f"No se pudo leer alguno de los archivos:\n{e}")
        return

    deferreds = []
    # Procesar cada fila del CSV y enviar el correo correspondiente
    for row in rows:
        if len(row) < 4:
            print(f"⚠ Fila inválida (se requieren 4 columnas): {row}")
            continue
        d = send_email(row, host, port, message_template, attachment_file=attachment)
        deferreds.append(d)

    if not deferreds:
        messagebox.showwarning("Advertencia", "No se encontraron filas válidas en el CSV.")
        return

    # Crear una lista diferida que se activará cuando todos los correos se hayan enviado
    dl = defer.DeferredList(deferreds)

    def all_done(results):
        messagebox.showinfo("Completado", "Todos los correos fueron enviados.")
        print("Todos los correos fueron enviados.")

    dl.addCallback(all_done)


# Configuración básica del log de Twisted para ver la salida en consola
log.startLogging(sys.stdout)

# Crear la ventana principal de Tkinter
root = tk.Tk()
root.title("Cliente SMTP con Twisted")

# Configurar la entrada para el servidor SMTP
label_host = tk.Label(root, text="Servidor SMTP:")
label_host.grid(row=0, column=0, padx=5, pady=5, sticky="w")
entry_host = tk.Entry(root, width=40)
entry_host.grid(row=0, column=1, padx=5, pady=5)

# Configurar la entrada para el puerto SMTP
label_port = tk.Label(root, text="Puerto:")
label_port.grid(row=1, column=0, padx=5, pady=5, sticky="w")
entry_port = tk.Entry(root, width=10)
entry_port.grid(row=1, column=1, padx=5, pady=5, sticky="w")
entry_port.insert(0, "25")  # Valor por defecto del puerto

# Configurar la entrada para seleccionar el archivo CSV
label_csv = tk.Label(root, text="Archivo CSV:")
label_csv.grid(row=2, column=0, padx=5, pady=5, sticky="w")
entry_csv = tk.Entry(root, width=40)
entry_csv.grid(row=2, column=1, padx=5, pady=5)
def browse_csv():
    """
    Función para abrir el diálogo de selección de archivo para el CSV.
    """
    filename = filedialog.askopenfilename(title="Selecciona el archivo CSV", filetypes=[("CSV Files", "*.csv")])
    if filename:
        entry_csv.delete(0, tk.END)
        entry_csv.insert(0, filename)
button_csv = tk.Button(root, text="Examinar", command=browse_csv)
button_csv.grid(row=2, column=2, padx=5, pady=5)

# Configurar la entrada para seleccionar el archivo de mensaje
label_msg = tk.Label(root, text="Archivo Mensaje:")
label_msg.grid(row=3, column=0, padx=5, pady=5, sticky="w")
entry_msg = tk.Entry(root, width=40)
entry_msg.grid(row=3, column=1, padx=5, pady=5)
def browse_msg():
    """
    Función para abrir el diálogo de selección de archivo para el mensaje.
    """
    filename = filedialog.askopenfilename(title="Selecciona el archivo de mensaje", filetypes=[("Text Files", "*.txt"), ("All Files", "*.*")])
    if filename:
        entry_msg.delete(0, tk.END)
        entry_msg.insert(0, filename)
button_msg = tk.Button(root, text="Examinar", command=browse_msg)
button_msg.grid(row=3, column=2, padx=5, pady=5)

# Configurar la entrada para seleccionar un archivo adjunto (opcional)
label_attachment = tk.Label(root, text="Archivo Adjunto (opcional):")
label_attachment.grid(row=4, column=0, padx=5, pady=5, sticky="w")
entry_attachment = tk.Entry(root, width=40)
entry_attachment.grid(row=4, column=1, padx=5, pady=5)
def browse_attachment():
    """
    Función para abrir el diálogo de selección de archivo para el adjunto.
    """
    filename = filedialog.askopenfilename(title="Selecciona el archivo adjunto")
    if filename:
        entry_attachment.delete(0, tk.END)
        entry_attachment.insert(0, filename)
button_attachment = tk.Button(root, text="Examinar", command=browse_attachment)
button_attachment.grid(row=4, column=2, padx=5, pady=5)

# Botón para iniciar el envío de correos
button_send = tk.Button(root, text="Enviar Correos", command=send_emails_callback)
button_send.grid(row=5, column=1, padx=5, pady=15)

# Integrar el reactor de Twisted con la ventana de Tkinter
tksupport.install(root)

# Iniciar el reactor de Twisted, que a su vez ejecuta la interfaz gráfica
reactor.run()
