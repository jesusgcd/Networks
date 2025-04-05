import argparse  # Para el manejo de argumentos de línea de comandos
import csv       # Para la lectura y procesamiento de archivos CSV
import os        # Para operaciones relacionadas con el sistema de archivos
from email.mime.text import MIMEText             # Para crear mensajes de correo en texto plano
from email.mime.multipart import MIMEMultipart       # Para crear mensajes de correo con múltiples partes (texto y adjuntos)
from email.mime.base import MIMEBase               # Para crear la parte base para adjuntos
from email import encoders                         # Para codificar en base64 los adjuntos
from twisted.internet import reactor, defer        # Para la programación asíncrona con Twisted
from twisted.mail.smtp import sendmail             # Para enviar correos electrónicos usando el protocolo SMTP a través de Twisted
from twisted.python import log                     # Para registrar logs en la consola
import sys                                         # Para el acceso a funciones e información del sistema

def parse_arguments():
    """
    Define y procesa los argumentos de línea de comandos.
    
    Argumentos:
    -H, --host:      Servidor SMTP al que conectarse (ej: localhost o dominio)
    -c, --csv:       Archivo CSV con la lista de correos (nombre, emisor, receptor, asunto)
    -m, --message:   Archivo que contiene la plantilla del mensaje a enviar
    -P, --port:      Puerto del servidor SMTP (por defecto: 25)
    -f, --file:      Archivo a adjuntar (opcional)
    
    Retorna:
    Los argumentos parseados.
    """
    parser = argparse.ArgumentParser(description="Cliente SMTP usando Twisted")
    
    parser.add_argument('-H', '--host', type=str, required=True,
                        help='Servidor SMTP al que conectarse (ej: localhost o dominio)')
    parser.add_argument('-c', '--csv', type=str, required=True,
                        help='Archivo CSV con la lista de correos (nombre, emisor, receptor, asunto)')
    parser.add_argument('-m', '--message', type=str, required=True,
                        help='Archivo que contiene la plantilla del mensaje a enviar')
    parser.add_argument('-P', '--port', type=int, default=25,
                        help='Puerto del servidor SMTP (por defecto: 25)')
    parser.add_argument('-f', '--file', type=str, required=False,
                        help='Archivo a adjuntar (opcional)')
    
    return parser.parse_args()


def send_email(row, host, port, message_template, attachment_file=None):
    """
    Envía un correo electrónico utilizando los datos de la fila CSV.
    
    Se espera que cada fila del CSV contenga:
      [nombre, emisor, receptor, asunto]
    
    El mensaje se personaliza reemplazando el marcador {nombre} en la plantilla.
    Si se especifica un archivo adjunto, se crea un mensaje multipart.
    
    Parámetros:
      row              -- Lista con los datos [nombre, emisor, receptor, asunto]
      host             -- Servidor SMTP
      port             -- Puerto del servidor SMTP
      message_template -- Plantilla del mensaje a enviar
      attachment_file  -- (Opcional) Ruta al archivo a adjuntar
    
    Retorna:
      Un Deferred que representa el envío del mensaje mediante sendmail.
    """
    nombre, sender, recipient, subject = row[0], row[1], row[2], row[3]
    
    # Personalizar el mensaje reemplazando el marcador {nombre} en la plantilla
    personalized_message = message_template.format(nombre=nombre)
    
    # Imprimir los datos del correo que se enviará para propósitos de depuración
    print(f"Enviando correo a {recipient} desde {sender} con asunto '{subject}'")
    print("Contenido del mensaje:")
    print(personalized_message)
    if attachment_file:
        print(f"Adjuntando archivo: {attachment_file}")
    print("-" * 40)
    
    # Crear el mensaje, considerando si se adjunta archivo o no
    if attachment_file:
        # Si se adjunta archivo, se crea un mensaje multipart
        msg = MIMEMultipart()
        msg["Subject"] = subject
        msg["From"] = sender
        msg["To"] = recipient

        # Agregar el cuerpo del mensaje en formato de texto plano
        body = MIMEText(personalized_message, "plain", "utf-8")
        msg.attach(body)

        # Intentar adjuntar el archivo si este existe
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
        # Si no hay archivo adjunto, se crea un mensaje simple de texto
        msg = MIMEText(personalized_message, "plain", "utf-8")
        msg["Subject"] = subject
        msg["From"] = sender
        msg["To"] = recipient

    # Enviar el mensaje utilizando sendmail de Twisted, codificando el mensaje en UTF-8
    return sendmail(host, sender, [recipient], msg.as_string().encode('utf-8'), port=port)


def main():
    """
    Función principal del programa.
    
    Realiza las siguientes acciones:
      1. Procesa los argumentos de línea de comandos.
      2. Lee la plantilla del mensaje y el archivo CSV.
      3. Envía un correo por cada registro del CSV.
      4. Espera a que todos los correos se hayan enviado para finalizar la ejecución.
    """
    args = parse_arguments()
    
    try:
        # Leer la plantilla del mensaje desde el archivo especificado
        with open(args.message, 'r', encoding='utf-8') as f:
            message_template = f.read()
        
        # Leer el archivo CSV que contiene los datos de los correos
        emails = []
        with open(args.csv, 'r', encoding='utf-8') as csvfile:
            reader = csv.reader(csvfile)
            rows = list(reader)
            # Verificar si la primera fila es una cabecera y omitirla en caso afirmativo
            if rows and rows[0][0].lower() == "nombre":
                print("Se detectó cabecera en el CSV, omitiendo la primera fila.")
                rows = rows[1:]
            emails.extend(rows)
        
        # Enviar un correo por cada registro del CSV, acumulando los Deferred resultantes
        deferreds = []
        for row in emails:
            # Validar que la fila tenga al menos 4 columnas
            if len(row) < 4:
                print(f"⚠ Fila inválida (se requieren 4 columnas): {row}")
                continue
            d = send_email(row, args.host, args.port, message_template, attachment_file=args.file)
            deferreds.append(d)
        
        # Esperar a que todos los envíos se completen usando un DeferredList
        dl = defer.DeferredList(deferreds)
        
        def all_done(results):
            # Callback que se ejecuta cuando todos los Deferred han finalizado
            print("Todos los correos fueron enviados.")
            reactor.stop()
        
        dl.addCallback(all_done)
        
        # Iniciar el reactor de Twisted para procesar los envíos de correos
        reactor.run()
    except Exception as e:
        # En caso de error, imprimir el mensaje de error y sugerir revisar los archivos
        print(f"Ocurrió un error: {e}")
        print("Revise el formato del archivo, del archivo CSV y del archivo de mensaje.")


if __name__ == "__main__":
    # Iniciar el registro de logs de Twisted en la salida estándar
    log.startLogging(sys.stdout)
    
    # Ejecutar la función principal del programa
    main()
