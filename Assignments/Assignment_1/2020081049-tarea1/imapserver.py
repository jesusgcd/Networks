import argparse
import os
import re
from email import message_from_string
from email.policy import default as default_policy
from email.utils import getaddresses
from twisted.internet import reactor, protocol

# Diccionario de usuarios para autenticación (usuario: contraseña)
USUARIOS = {
    "santa@polonorte.com": "password123",
    "santa@example.com": "password123",
    "senoraClous@polonorte.com": "password123"
}

def parse_address_list(header_value):
    """
    Convierte un encabezado (From, To, etc.) en una lista IMAP de direcciones.
    Formato IMAP: (( "personal" NIL "mailbox" "host" ) ( ... ))
    Si no hay direcciones se devuelve NIL (sin comillas).
    Si el display name está vacío se usa el local part como nombre.
    """
    if not header_value:
        return "NIL"
    addresses = getaddresses([header_value])
    if not addresses:
        return "NIL"
    parts = []
    for display_name, email_addr in addresses:
        email_addr = email_addr.strip()
        # Si no hay display name, usamos la parte local de la dirección
        if not display_name:
            if "@" in email_addr:
                local, domain = email_addr.split("@", 1)
                display_name = local
            else:
                display_name = email_addr
        # Escapamos y ponemos entre comillas
        display_name = display_name.strip('" ')
        display_name = f'"{display_name}"'
        if "@" in email_addr:
            local, domain = email_addr.split("@", 1)
            sub_list = f'({display_name} NIL "{local}" "{domain}")'
        else:
            sub_list = "NIL"
        parts.append(sub_list)
    return f'({" ".join(parts)})'

def _build_envelope(msg, uid):
    """
    Construye el sobre (envelope) de un mensaje de correo electrónico en formato IMAP.
    Args:
        msg (email.message.Message): El mensaje de correo electrónico del cual se extraerán los datos.
        uid (int): El identificador único del mensaje.
    Returns:
        str: Una cadena que representa el sobre del mensaje en formato IMAP.
    El sobre incluye los siguientes campos:
        1) Date: La fecha del mensaje en formato IMAP. Si no está disponible, se usa la fecha actual.
        2) Subject: El asunto del mensaje. Si no está disponible, se usa "NIL".
        3) From: La dirección del remitente.
        4) Sender: Se deja en "NIL".
        5) Reply-To: Se usa la misma dirección que "From".
        6) To: La dirección del destinatario.
        7) Cc: Se deja en "NIL".
        8) Bcc: Se deja en "NIL".
        9) In-Reply-To: Se deja en "NIL".
        10) Message-ID: El identificador del mensaje. Si no está disponible, se genera uno basado en el UID.
    """
    # 1) Date
    date_value = msg.get("Date")
    if date_value:
        date_value = date_value.replace('"', '\\"')
        date_value = f'"{date_value}"'
    else:
        # Si no hay fecha, usar la fecha actual en un formato IMAP adecuado
        import email.utils, time
        now = email.utils.formatdate(time.time(), localtime=True)
        date_value = f'"{now}"'
    
    # 2) Subject
    subject_value = msg.get("Subject")
    if subject_value:
        subject_value = subject_value.replace('"', '\\"')
        subject_value = f'"{subject_value}"'
    else:
        subject_value = "NIL"

    # 3) From
    from_value = msg.get("From")
    from_list = parse_address_list(from_value)
    
    # Los campos Sender y Reply-To se dejan en NIL o se usa From
    sender_list = "NIL"
    reply_to_list = from_list
    
    # 6) To
    to_value = msg.get("To")
    to_list = parse_address_list(to_value)
    
    # 7) Cc, 8) Bcc, 9) In-Reply-To
    cc_list = "NIL"
    bcc_list = "NIL"
    in_reply_to_value = "NIL"
    
    # 10) Message-ID
    msg_id = msg.get("Message-ID")
    if msg_id:
        msg_id = msg_id.replace('"', '\\"')
        msg_id = f'"{msg_id}"'
    else:
        msg_id = f'"<msg{uid}@example.com>"'
    
    envelope = (
        f'({date_value} {subject_value} {from_list} {sender_list} '
        f'{reply_to_list} {to_list} {cc_list} {bcc_list} {in_reply_to_value} {msg_id})'
    )
    return envelope

def _extract_text(msg):
    """
    Extrae la primera parte text/plain sin filename de un mensaje multipart;
    si no es multipart, devuelve el payload decodificado.
    """
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain" and not part.get_filename():
                return part.get_payload(decode=True).decode(
                    part.get_content_charset() or "utf-8", errors="replace"
                )
        return msg.get_payload(decode=True).decode(
            msg.get_content_charset() or "utf-8", errors="replace"
        )
    else:
        return msg.get_payload(decode=True).decode(
            msg.get_content_charset() or "utf-8", errors="replace"
        )

def _extract_headers(msg):
    """
    Reconstruye un bloque de encabezados a partir del mensaje.
    """
    headers = ""
    for key, value in msg.items():
        headers += f"{key}: {value}\r\n"
    return headers

class IMAPProtocol(protocol.Protocol):
    """
    Clase IMAPProtocol que implementa el protocolo IMAP para un servidor de correo.
    Atributos:
        logged_in (bool): Indica si el usuario ha iniciado sesión.
        user_mail_dir (str): Directorio de correo del usuario autenticado.
        msg_files (list): Lista de archivos de mensajes en el buzón del usuario.
        msg_flags (dict): Diccionario que almacena las banderas de cada mensaje (clave: número de mensaje, valor: conjunto de banderas).
    Métodos:
        connectionMade(): Método llamado cuando se establece una conexión con el servidor. Envía un mensaje al cliente indicando que el servicio IMAP4rev1 está listo.
        dataReceived(data): Método llamado cuando se reciben datos. Procesa los datos recibidos y maneja los comandos IMAP.
        handle_capability(tag): Maneja el comando CAPABILITY del servidor IMAP. Responde con las capacidades del servidor.
        handle_status(tag, mailbox, items): Maneja el comando STATUS del servidor IMAP. Responde con el estado del buzón de correo.
        handle_login(tag, username, password): Maneja el proceso de inicio de sesión de un usuario. Verifica las credenciales y establece el estado de sesión.
        handle_select(tag, mailbox): Maneja el comando SELECT del protocolo IMAP. Selecciona un buzón de correo especificado por el cliente.
        handle_fetch(tag, msg_num, fetch_args): Maneja el comando FETCH del protocolo IMAP. Recupera mensajes del buzón seleccionado.
        handle_uid_fetch(tag, command): Maneja el comando UID FETCH del servidor IMAP. Recupera mensajes basados en sus identificadores únicos (UID).
        handle_lsub(tag): Maneja el comando LSUB del protocolo IMAP. Lista los buzones suscritos.
        handle_list(tag): Maneja el comando LIST del servidor IMAP. Lista todos los buzones disponibles para el usuario autenticado.
        handle_logout(tag): Maneja el comando LOGOUT del cliente IMAP. Cierra la sesión del cliente y la conexión de transporte.
        handle_noop(tag): Maneja el comando NOOP del servidor IMAP. Responde con una confirmación de que el comando NOOP se ha completado.
        handle_expunge(tag): Maneja el comando EXPUNGE del servidor IMAP. Elimina permanentemente los mensajes marcados con la bandera Deleted.
        handle_store(tag, msg_num, store_args): Maneja el comando STORE del protocolo IMAP. Actualiza las banderas de un mensaje.
        handle_search(tag, search_args): Maneja el comando SEARCH del protocolo IMAP. Busca mensajes que coincidan con los criterios dados.
        """
    def __init__(self):
        self.logged_in = False
        self.user_mail_dir = None
        self.msg_files = []
        # Diccionario para almacenar flags de cada mensaje (clave: número de mensaje, valor: set de flags)
        self.msg_flags = {}

    def connectionMade(self):
        """
        Este método se llama cuando se establece una conexión con el servidor.

        Envía un mensaje al cliente indicando que el servicio IMAP4rev1 está listo.

        No tiene parámetros ni valores de retorno.
        """
        self.transport.write(b"* OK IMAP4rev1 Service Ready\r\n")

    def dataReceived(self, data):
        """
        Método llamado cuando se reciben datos.
        Este método procesa los datos recibidos, decodifica el comando y lo maneja
        según el tipo de comando IMAP recibido. Los comandos soportados incluyen:
        CAPABILITY, AUTHENTICATE, LOGIN, SELECT, FETCH, UID, LSUB, LIST, STATUS,
        LOGOUT, NOOP, EXPUNGE, STORE y SEARCH.
        Args:
            data (bytes): Los datos recibidos desde el cliente.
        Comandos soportados:
            - CAPABILITY: Maneja la solicitud de capacidades del servidor.
            - AUTHENTICATE: Maneja la autenticación, actualmente solo soporta LOGIN.
            - LOGIN: Maneja el inicio de sesión con nombre de usuario y contraseña.
            - SELECT: Selecciona un buzón de correo.
            - FETCH: Recupera mensajes del buzón seleccionado.
            - UID: Maneja comandos UID FETCH y UID STORE.
            - LSUB: Lista los buzones suscritos.
            - LIST: Lista todos los buzones.
            - STATUS: Recupera el estado de un buzón.
            - LOGOUT: Cierra la sesión del cliente.
            - NOOP: No realiza ninguna operación, mantiene la conexión activa.
            - EXPUNGE: Elimina mensajes marcados para eliminación.
            - STORE: Almacena datos en un mensaje.
            - SEARCH: Busca mensajes que coincidan con los criterios dados.
        Si el comando no es reconocido, se envía una respuesta BAD al cliente.
        """
        print("*" * 20)
        print("Data recibida:", data)
        print("*" * 20)
        command = data.decode().strip()
        print("Comando recibido:", command)
        tokens = command.split()
        if not tokens:
            return
        tag = tokens[0]
        comando = tokens[1].upper() if len(tokens) > 1 else ""
        
        if comando == "CAPABILITY":
            self.handle_capability(tag)
        elif comando == "AUTHENTICATE" and len(tokens) >= 3 and tokens[2].upper() == "PLAIN":
            self.transport.write(f"{tag} NO AUTHENTICATE PLAIN no soportado, use LOGIN\r\n".encode())
        elif comando == "LOGIN" and len(tokens) >= 4:
            username = tokens[2].strip('"')
            password = tokens[3].strip('"')
            self.handle_login(tag, username, password)
        elif comando == "SELECT" and len(tokens) >= 3:
            self.handle_select(tag, tokens[2])
        elif comando == "FETCH" and len(tokens) >= 4:
            self.handle_fetch(tag, tokens[2], " ".join(tokens[3:]))
        elif comando == "UID" and len(tokens) >= 4:
            # Se manejan UID FETCH y UID STORE
            subcomando = tokens[2].upper()
            if subcomando == "FETCH":
                self.handle_uid_fetch(tag, command)
            elif subcomando == "STORE":
                # Formato esperado: <tag> UID STORE <msg_num> <store_args>
                store_args = " ".join(tokens[4:])
                self.handle_store(tag, tokens[3], store_args)
            else:
                response = f"{tag} BAD UID comando no reconocido\r\n"
                self.transport.write(response.encode())
        elif comando == "LSUB":
            self.handle_lsub(tag)
        elif comando == "LIST":
            self.handle_list(tag)
        elif comando == "STATUS" and len(tokens) >= 4:
            mailbox = tokens[2].strip('"')
            items = " ".join(tokens[3:])
            self.handle_status(tag, mailbox, items)
        elif comando == "LOGOUT":
            self.handle_logout(tag)
        elif comando == "NOOP":
            self.handle_noop(tag)
        elif comando == "EXPUNGE":
            self.handle_expunge(tag)
        elif comando == "STORE" and len(tokens) >= 4:
            self.handle_store(tag, tokens[2], " ".join(tokens[3:]))
        elif comando == "SEARCH":
            self.handle_search(tag, " ".join(tokens[2:]))
        else:
            response = f"{tag} BAD Comando no reconocido\r\n"
            self.transport.write(response.encode())

    def handle_capability(self, tag):
        """
        Maneja el comando CAPABILITY del servidor IMAP.

        Este método responde con las capacidades del servidor IMAP, indicando
        que soporta IMAP4rev1 y autenticación PLAIN.

        Parámetros:
        tag (str): El identificador de la solicitud del cliente.

        Respuesta:
        Escribe la respuesta de capacidades y una confirmación de finalización
        al cliente a través del transporte.
        """
        response = "* CAPABILITY IMAP4rev1 AUTH=PLAIN\r\n"
        self.transport.write(response.encode())
        self.transport.write(f"{tag} OK CAPABILITY completed\r\n".encode())

    def handle_status(self, tag, mailbox, items):
        """
        Maneja el comando STATUS del servidor IMAP.

        Parámetros:
        tag (str): El tag asociado con el comando IMAP.
        mailbox (str): El nombre del buzón de correo para el cual se solicita el estado.
        items (list): Lista de ítems solicitados en el comando STATUS (no se usa en esta implementación).

        Responde con el estado del buzón de correo, incluyendo el número de mensajes, el UIDNEXT, 
        y los contadores de mensajes no vistos y recientes (ambos establecidos en 0).

        La respuesta se envía a través del transporte asociado al servidor.

        """
        num_msgs = len(self.msg_files)
        uidnext = num_msgs + 1
        response = (f"* STATUS \"{mailbox}\" (MESSAGES {num_msgs} UIDNEXT {uidnext} UNSEEN 0 RECENT 0)\r\n"
                    f"{tag} OK STATUS completed\r\n")
        self.transport.write(response.encode())

    def handle_login(self, tag, username, password):
        """
        Maneja el proceso de inicio de sesión de un usuario.

        Parámetros:
        tag (str): Etiqueta del comando IMAP.
        username (str): Nombre de usuario proporcionado para el inicio de sesión.
        password (str): Contraseña proporcionada para el inicio de sesión.

        Comportamiento:
        - Verifica si el nombre de usuario y la contraseña coinciden con los almacenados en USUARIOS.
        - Si las credenciales son correctas, marca al usuario como conectado (logged_in).
        - Divide el nombre de usuario en parte local y dominio.
        - Verifica si el directorio de correo del usuario existe.
        - Si el directorio existe, lista y ordena los archivos de mensajes (.eml) y los inicializa con sus flags.
        - Si el directorio no existe, inicializa las listas de mensajes y flags como vacías.
        - Envía una respuesta de éxito o fallo de inicio de sesión al cliente.

        Respuestas:
        - "{tag} OK LOGIN completado\r\n" si el inicio de sesión es exitoso.
        - "{tag} NO LOGIN fallido\r\n" si el inicio de sesión falla.
        - "{tag} NO Formato de usuario incorrecto\r\n" si el formato del nombre de usuario es incorrecto.
        """
        if username in USUARIOS and USUARIOS[username] == password:
            self.logged_in = True
            try:
                local_part, domain = username.split("@")
            except ValueError:
                response = f"{tag} NO Formato de usuario incorrecto\r\n"
                self.transport.write(response.encode())
                return
            self.user_mail_dir = os.path.join(self.factory.mail_storage, domain, local_part)
            if os.path.isdir(self.user_mail_dir):
                self.msg_files = sorted([f for f in os.listdir(self.user_mail_dir) if f.lower().endswith(".eml")])
                # Inicializar los flags para cada mensaje
                self.msg_flags = {i + 1: set() for i in range(len(self.msg_files))}
                print(f"Se encontraron {len(self.msg_files)} mensajes en {self.user_mail_dir}")
            else:
                self.msg_files = []
                self.msg_flags = {}
            response = f"{tag} OK LOGIN completado\r\n"
        else:
            response = f"{tag} NO LOGIN fallido\r\n"
        self.transport.write(response.encode())

    def handle_select(self, tag, mailbox):
        """
        Maneja el comando SELECT del protocolo IMAP.

        Este método selecciona un buzón de correo especificado por el cliente.
        Si el usuario no ha iniciado sesión, se le informa que debe autenticarse primero.
        Si el buzón seleccionado es "INBOX", se envía información sobre el estado del buzón,
        incluyendo el número de mensajes, las banderas disponibles, la validez de los UIDs y el próximo UID predicho.
        Si el buzón no se encuentra, se envía una respuesta indicando que la carpeta no fue encontrada.

        Args:
            tag (str): La etiqueta del comando IMAP.
            mailbox (str): El nombre del buzón de correo a seleccionar.

        Returns:
            None
        """
        if not self.logged_in:
            response = f"{tag} NO Debe autenticarse primero\r\n"
            self.transport.write(response.encode())
            return
        mailbox = mailbox.strip('"').upper()
        print("Mailbox seleccionado:", mailbox)
        if mailbox == "INBOX":
            num_msgs = len(self.msg_files)
            uidnext = num_msgs + 1
            self.transport.write(b"* FLAGS (\\Answered \\Flagged \\Draft \\Deleted \\Seen)\r\n")
            self.transport.write(f"* {num_msgs} EXISTS\r\n".encode())
            self.transport.write(f"* OK [UIDVALIDITY 1] UIDs valid\r\n".encode())
            self.transport.write(f"* OK [UIDNEXT {uidnext}] Predicted next UID\r\n".encode())
            self.transport.write(f"{tag} OK [READ-WRITE] SELECT completed\r\n".encode())
        else:
            response = f"{tag} NO Carpeta no encontrada\r\n"
            self.transport.write(response.encode())

    def handle_fetch(self, tag, msg_num, fetch_args):
        """
        Maneja el comando FETCH del protocolo IMAP.

        Parámetros:
        tag (str): El tag del comando IMAP.
        msg_num (str): El número del mensaje a recuperar.
        fetch_args (str): Los argumentos del comando FETCH, especificando qué partes del mensaje recuperar.

        Comportamiento:
        - Verifica si el usuario está autenticado. Si no lo está, responde con un mensaje de error.
        - Intenta convertir el número del mensaje a un entero. Si falla, responde con un mensaje de error.
        - Verifica si el número del mensaje está dentro del rango válido. Si no lo está, responde con un mensaje de error.
        - Lee el contenido del mensaje desde el archivo correspondiente.
        - Dependiendo de los argumentos de FETCH, extrae y responde con la parte correspondiente del mensaje:
            - "RFC822" o "BODY[]": Responde con el contenido completo del mensaje.
            - "BODY[HEADER]": Responde con los encabezados del mensaje.
            - "BODY[TEXT]": Responde con el cuerpo del mensaje.
        - Si los argumentos de FETCH no coinciden con ninguno de los anteriores, responde con el contenido completo del mensaje.
        - Finalmente, responde con un mensaje indicando que el comando FETCH se completó con éxito.

        Respuestas de error:
        - Si el usuario no está autenticado: "{tag} NO Debe autenticarse primero\r\n"
        - Si el número del mensaje no es válido: "{tag} BAD Número de mensaje inválido\r\n"
        - Si el mensaje no se encuentra: "{tag} NO Mensaje no encontrado\r\n"
        - Si ocurre un error al leer el mensaje: "{tag} NO Error al leer el mensaje: {e}\r\n"
        """
        if not self.logged_in:
            response = f"{tag} NO Debe autenticarse primero\r\n"
            self.transport.write(response.encode())
            return
        try:
            msg_num = int(msg_num)
        except ValueError:
            response = f"{tag} BAD Número de mensaje inválido\r\n"
            self.transport.write(response.encode())
            return
        index = msg_num - 1
        if index < 0 or index >= len(self.msg_files):
            response = f"{tag} NO Mensaje no encontrado\r\n"
            self.transport.write(response.encode())
            return

        msg_filename = self.msg_files[index]
        msg_path = os.path.join(self.user_mail_dir, msg_filename)
        print("Leyendo mensaje:", msg_path)
        try:
            with open(msg_path, "r", encoding="utf-8") as f:
                raw_content = f.read()
        except Exception as e:
            response = f"{tag} NO Error al leer el mensaje: {e}\r\n"
            self.transport.write(response.encode())
            return

        msg = message_from_string(raw_content, policy=default_policy)
        fetch_args = fetch_args.upper()
        if "RFC822" in fetch_args or "BODY[]" in fetch_args:
            content = raw_content
            literal = f"{{{len(content.encode('utf-8'))}}}"
            response = (f"* {msg_num} FETCH (RFC822 {literal}\r\n"
                        f"{content}\r\n)\r\n")
            self.transport.write(response.encode())
        elif "BODY[HEADER]" in fetch_args:
            headers = _extract_headers(msg)
            literal = f"{{{len(headers.encode('utf-8'))}}}"
            response = (f"* {msg_num} FETCH (BODY[HEADER] {literal}\r\n"
                        f"{headers}\r\n)\r\n")
            self.transport.write(response.encode())
        elif "BODY[TEXT]" in fetch_args:
            body = _extract_text(msg)
            literal = f"{{{len(body.encode('utf-8'))}}}"
            response = (f"* {msg_num} FETCH (BODY[TEXT] {literal}\r\n"
                        f"{body}\r\n)\r\n")
            self.transport.write(response.encode())
        else:
            content = raw_content
            literal = f"{{{len(content.encode('utf-8'))}}}"
            response = (f"* {msg_num} FETCH (RFC822 {literal}\r\n"
                        f"{content}\r\n)\r\n")
            self.transport.write(response.encode())

        self.transport.write(f"{tag} OK FETCH completed\r\n".encode())

    def handle_uid_fetch(self, tag, command):
        """
        Maneja el comando UID FETCH del servidor IMAP.

        Este método procesa el comando UID FETCH, que se utiliza para recuperar mensajes de correo electrónico
        basados en sus identificadores únicos (UID). Dependiendo de los parámetros del comando, puede recuperar
        diferentes partes del mensaje, como los encabezados o el contenido completo.

        Parámetros:
        - tag (str): La etiqueta del comando IMAP.
        - command (str): El comando completo recibido del cliente.

        Comportamiento:
        - Analiza el comando para determinar el rango de UIDs a recuperar.
        - Lee los archivos de mensajes correspondientes a los UIDs especificados.
        - Construye y envía la respuesta adecuada al cliente, incluyendo los encabezados y/o el contenido del mensaje.

        Excepciones:
        - Si ocurre un error al leer un archivo de mensaje, el contenido del mensaje se establece como una cadena vacía.
        - Si el comando no especifica un rango de UIDs válido, se recuperan todos los mensajes disponibles.

        Respuesta:
        - Escribe la respuesta al cliente a través del transporte, indicando el estado de la operación y los datos solicitados.
        """
        command_upper = command.upper()
        m = re.search(r'UID fetch (\S+)\s+\(', command, re.IGNORECASE)
        if m:
            seq_set = m.group(1)
            if ':' in seq_set:
                start_str, end_str = seq_set.split(':', 1)
                try:
                    start = int(start_str)
                    end = int(end_str) if end_str != '*' else len(self.msg_files)
                except Exception:
                    start = 1
                    end = len(self.msg_files)
                uids = range(start, end + 1)
            else:
                try:
                    uid_val = int(seq_set)
                    uids = [uid_val]
                except:
                    uids = range(1, len(self.msg_files) + 1)
        else:
            uids = range(1, len(self.msg_files) + 1)

        for i, msg_filename in enumerate(self.msg_files):
            uid = i + 1
            if uid not in uids:
                continue
            msg_path = os.path.join(self.user_mail_dir, msg_filename)
            try:
                with open(msg_path, "r", encoding="utf-8") as f:
                    raw_content = f.read()
            except Exception as e:
                raw_content = ""
            msg = message_from_string(raw_content, policy=default_policy)
            size = len(raw_content.encode("utf-8"))
            if "BODY.PEEK[HEADER.FIELDS" in command_upper:
                headers = _extract_headers(msg)
                header_size = len(headers.encode("utf-8"))
                envelope = _build_envelope(msg, uid)
                internaldate = '"01-Jan-2020 00:00:00 +0000"'
                literal = f"{{{header_size}}}"
                response = (f"* {uid} FETCH (UID {uid} RFC822.SIZE {size} FLAGS () INTERNALDATE {internaldate} "
                            f"ENVELOPE {envelope} BODY.PEEK[HEADER.FIELDS (FROM TO CC BCC SUBJECT DATE MESSAGE-ID PRIORITY X-PRIORITY REFERENCES NEWGROUPS IN-REPLY-TO CONTENT-TYPE REPLY-TO)] {literal}\r\n"
                            f"{headers}\r\n)\r\n")
            elif "RFC822" in command_upper:
                envelope = _build_envelope(msg, uid)
                literal = f"{{{size}}}"
                response = (f"* {uid} FETCH (UID {uid} RFC822.SIZE {size} FLAGS () ENVELOPE {envelope} RFC822 {literal}\r\n"
                            f"{raw_content}\r\n)\r\n")
            else:
                response = f"* {uid} FETCH (UID {uid} FLAGS ())\r\n"
            self.transport.write(response.encode())

        self.transport.write(f"{tag} OK UID FETCH completed\r\n".encode())

    def handle_lsub(self, tag):
        """
        Maneja el comando LSUB del protocolo IMAP.

        Parámetros:
        tag (str): El identificador de la etiqueta del comando IMAP.

        Comportamiento:
        - Si el usuario no ha iniciado sesión (logged_in es False), envía una respuesta de error indicando que debe autenticarse primero.
        - Si el usuario ha iniciado sesión, envía una respuesta indicando que la carpeta INBOX no tiene subcarpetas y que el comando LSUB se completó correctamente.
        """
        if not self.logged_in:
            response = f"{tag} NO Debe autenticarse primero\r\n"
            self.transport.write(response.encode())
            return
        self.transport.write(b"* LSUB (\\HasNoChildren) \"/\" INBOX\r\n")
        self.transport.write(f"{tag} OK LSUB completed\r\n".encode())

    def handle_list(self, tag):
        """
        Maneja el comando LIST del servidor IMAP.

        Este método responde con la lista de buzones disponibles para el usuario autenticado.
        Si el usuario no está autenticado, se envía un mensaje de error indicando que debe autenticarse primero.

        Parámetros:
        tag (str): El identificador de la solicitud IMAP.

        Respuestas:
        - Si el usuario no está autenticado:
          "{tag} NO Debe autenticarse primero\r\n"
        - Si el usuario está autenticado:
          "* LIST (\\HasNoChildren) \"/\" \"INBOX\"\r\n"
          "{tag} OK LIST completed\r\n"
        """
        if not self.logged_in:
            response = f"{tag} NO Debe autenticarse primero\r\n"
            self.transport.write(response.encode())
            return
        self.transport.write(b'* LIST (\\HasNoChildren) "/" "INBOX"\r\n')
        self.transport.write(f"{tag} OK LIST completed\r\n".encode())

    def handle_logout(self, tag):
        """
        Maneja el comando LOGOUT del cliente IMAP.

        Envía un mensaje de despedida al cliente, indicando que el servidor IMAP4rev1
        se está desconectando, seguido de una confirmación de que el comando LOGOUT
        se ha completado. Luego, cierra la conexión de transporte.

        Parámetros:
        tag (str): El identificador de la etiqueta del comando IMAP enviado por el cliente.
        """
        self.transport.write(b"* BYE IMAP4rev1 Server logging out\r\n")
        self.transport.write(f"{tag} OK LOGOUT completed\r\n".encode())
        self.transport.loseConnection()

    def handle_noop(self, tag):
        """
        Maneja el comando NOOP del servidor IMAP.

        Este método responde al cliente con un mensaje de confirmación
        indicando que el comando NOOP se ha completado correctamente.

        Parámetros:
        tag (str): La etiqueta del comando NOOP enviado por el cliente.

        Respuesta:
        Escribe una respuesta al cliente en el formato "{tag} OK NOOP completed\r\n".
        """
        self.transport.write(f"{tag} OK NOOP completed\r\n".encode())

    def handle_expunge(self, tag):
        """
        Maneja el comando EXPUNGE del servidor IMAP.

        Este método elimina permanentemente todos los mensajes marcados con la bandera Deleted
        del buzón del usuario actualmente autenticado.

        Parámetros:
        tag (str): El tag asociado con el comando IMAP enviado por el cliente.

        Comportamiento:
        - Si el usuario no está autenticado, envía una respuesta de error al cliente.
        - Recorre todos los mensajes del buzón del usuario y elimina aquellos que tengan la bandera Deleted.
        - Actualiza la lista de archivos de mensajes y sus banderas correspondientes.
        - Envía una respuesta de éxito al cliente indicando que el comando EXPUNGE se completó.

        Excepciones:
        - Si ocurre un error al eliminar un archivo, se imprime un mensaje de error en la consola.
        """
        if not self.logged_in:
            response = f"{tag} NO Debe autenticarse primero\r\n"
            self.transport.write(response.encode())
            return
        # Recorre todos los mensajes y elimina aquellos que tengan la bandera \Deleted
        indices_a_expungir = []
        for i, msg_filename in enumerate(self.msg_files, start=1):
            if self.msg_flags.get(i) and "\\Deleted" in self.msg_flags[i]:
                file_path = os.path.join(self.user_mail_dir, msg_filename)
                try:
                    os.remove(file_path)
                    print(f"Eliminado archivo: {file_path}")
                    indices_a_expungir.append(i)
                except Exception as e:
                    print(f"Error al eliminar {file_path}: {e}")
        # Actualiza la lista de mensajes y los flags (en orden inverso para preservar índices)
        for indice in sorted(indices_a_expungir, reverse=True):
            del self.msg_files[indice - 1]
            del self.msg_flags[indice]
        self.transport.write(f"{tag} OK EXPUNGE completed\r\n".encode())

    def handle_store(self, tag, msg_num, store_args):
        """
        Maneja el comando STORE del protocolo IMAP para actualizar las banderas de un mensaje.
        Parámetros:
        tag (str): El tag del comando IMAP.
        msg_num (str): El número del mensaje al que se aplicarán las banderas.
        store_args (str): Los argumentos del comando STORE, que especifican las banderas a añadir o eliminar.
        Comportamiento:
        - Si el usuario no está autenticado, responde con un mensaje de error.
        - Si el número de mensaje no es válido, responde con un mensaje de error.
        - Asegura que existe una entrada de banderas para el mensaje.
        - Actualiza las banderas del mensaje según se indique (+FLAGS o -FLAGS).
        - Responde con las banderas actuales del mensaje.
        - Si el mensaje se marca con Deleted, elimina el archivo correspondiente y actualiza las estructuras de datos.
        Excepciones:
        - ValueError: Si el número de mensaje no es un entero válido.
        - Exception: Si ocurre un error al eliminar el archivo del mensaje marcado como Deleted.
        """
        if not self.logged_in:
            response = f"{tag} NO Debe autenticarse primero\r\n"
            self.transport.write(response.encode())
            return
        try:
            msg_num_int = int(msg_num)
        except ValueError:
            response = f"{tag} BAD Número de mensaje inválido\r\n"
            self.transport.write(response.encode())
            return
        # Asegurarse de que existe una entrada de flags para el mensaje
        if msg_num_int not in self.msg_flags:
            self.msg_flags[msg_num_int] = set()
        # Actualiza las banderas según se indique (+FLAGS o -FLAGS)
        if "+FLAGS" in store_args.upper():
            m = re.search(r'\((.*?)\)', store_args)
            if m:
                flags_str = m.group(1)
                flags = flags_str.split()
                self.msg_flags[msg_num_int].update(flags)
        elif "-FLAGS" in store_args.upper():
            m = re.search(r'\((.*?)\)', store_args)
            if m:
                flags_str = m.group(1)
                flags = flags_str.split()
                self.msg_flags[msg_num_int].difference_update(flags)
        # Responde con los flags actuales
        flags_list = sorted(self.msg_flags[msg_num_int])
        flags_response = " ".join(flags_list)
        self.transport.write(f"* {msg_num_int} FETCH (FLAGS ({flags_response}))\r\n".encode())
        self.transport.write(f"{tag} OK STORE completed\r\n".encode())
        
        # Si se ha marcado el mensaje con \Deleted, eliminar el archivo inmediatamente
        if "\\Deleted" in self.msg_flags[msg_num_int]:
            file_path = os.path.join(self.user_mail_dir, self.msg_files[msg_num_int - 1])
            try:
                os.remove(file_path)
                print(f"Eliminado archivo: {file_path}")
            except Exception as e:
                print(f"Error al eliminar {file_path}: {e}")
            # Elimina el mensaje de la lista y de los flags
            del self.msg_files[msg_num_int - 1]
            del self.msg_flags[msg_num_int]
            # Reindexar los índices de msg_flags para los mensajes restantes
            new_flags = {}
            for i, _ in enumerate(self.msg_files, start=1):
                new_flags[i] = set()
            self.msg_flags = new_flags

    def handle_search(self, tag, search_args):
        if not self.logged_in:
            response = f"{tag} NO Debe autenticarse primero\r\n"
            self.transport.write(response.encode())
            return
        self.transport.write(b"* SEARCH 1 2 3\r\n")
        self.transport.write(f"{tag} OK SEARCH completed\r\n".encode())

class IMAPFactory(protocol.Factory):
    """
    Una fábrica para crear instancias de IMAPProtocol.

    Args:
        mail_storage: Un objeto que representa el almacenamiento de correos.

    Métodos:
        buildProtocol(addr):
            Construye y devuelve una instancia de IMAPProtocol.
            Args:
                addr: La dirección del cliente.
            Returns:
                Una instancia de IMAPProtocol.
    """
    def __init__(self, mail_storage):
        self.mail_storage = mail_storage

    def buildProtocol(self, addr):
        proto = IMAPProtocol()
        proto.factory = self
        return proto

def parse_arguments():
    """
    Analiza los argumentos de la línea de comandos.

    Este método configura y analiza los argumentos necesarios para ejecutar el servidor IMAP.

    Argumentos:
        -s, --mail-storage (str): Directorio base para el almacenamiento de correos (ejemplo: ./var/mail). Este argumento es obligatorio.
        -p, --port (int): Puerto en el que se ejecutará el servidor IMAP (ejemplo: 143). Este argumento es obligatorio.

    Devuelve:
        Namespace: Un objeto Namespace con los argumentos analizados.
    """
    parser = argparse.ArgumentParser(description="Servidor IMAP usando Twisted (sin SSL/TLS)")
    parser.add_argument('-s', '--mail-storage', type=str, required=True,
                        help='Directorio base para el almacenamiento de correos (ejemplo: ./var/mail)')
    parser.add_argument('-p', '--port', type=int, required=True,
                        help='Puerto en el que se ejecutará el servidor IMAP (ejemplo: 143)')
    return parser.parse_args()

def main():
    """
    Función principal que inicia el servidor IMAP.

    Esta función realiza las siguientes acciones:
    1. Analiza los argumentos de la línea de comandos.
    2. Verifica si el directorio de almacenamiento de correos existe.
    3. Configura el servidor IMAP para escuchar en el puerto especificado.
    4. Inicia el reactor para manejar las conexiones entrantes.

    Si el directorio de almacenamiento de correos no existe, la función imprime un mensaje de error y termina la ejecución.

    Argumentos:
    - Ninguno. Los argumentos se obtienen mediante la función parse_arguments().

    Retorno:
    - Ninguno. La función no retorna ningún valor.
    """
    args = parse_arguments()
    mail_storage = args.mail_storage
    port = args.port
    if not os.path.isdir(mail_storage):
        print(f"El directorio {mail_storage} no existe")
        exit(1)
    reactor.listenTCP(port, IMAPFactory(mail_storage))
    print(f"Servidor IMAP corriendo en el puerto {port}")
    reactor.run()

if __name__ == "__main__":
    main()