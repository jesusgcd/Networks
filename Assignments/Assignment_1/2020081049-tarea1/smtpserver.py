import os  # Para operaciones de sistema y manejo de rutas
import argparse  # Para procesar argumentos de línea de comandos
from datetime import datetime  # Para generar marcas de tiempo en los nombres de archivos
from email import message_from_bytes  # Para crear objetos de mensaje desde bytes (no se usa directamente aquí)
from email.parser import BytesParser  # Para parsear mensajes en formato MIME desde bytes
from zope.interface import implementer  # Para implementar interfaces en clases

from twisted.cred.checkers import InMemoryUsernamePasswordDatabaseDontUse  # Checker de autenticación simple
from twisted.cred.portal import IRealm, Portal  # Para manejar autenticación y autorización
from twisted.internet import defer, reactor  # Para programación asíncrona y control de eventos con Twisted
from twisted.mail import smtp  # Para implementar funcionalidades de SMTP
from twisted.mail.imap4 import LOGINCredentials, PLAINCredentials  # Para manejo de credenciales de autenticación

def parse_arguments():
    """
    Procesa los argumentos de línea de comandos para configurar el servidor SMTP.
    
    Argumentos:
      -d, --domains:      Lista de dominios aceptados (separados por coma)
      -s, --mail-storage: Directorio donde se almacenarán los correos
      -p, --port:         Puerto en el que se ejecutará el servidor SMTP
    Retorna:
      Los argumentos parseados.
    """
    # Crear un parser para los argumentos de línea de comandos
    parser = argparse.ArgumentParser(description="SMTP Server usando Twisted")

    # Agregar argumento para los dominios aceptados
    parser.add_argument('-d', '--domains', type=str, required=True,
                        help='Dominios aceptados por el servidor (separados por coma)')
    
    # Agregar argumento para el directorio de almacenamiento de correos
    parser.add_argument('-s', '--mail-storage', type=str, required=True,
                        help='Directorio de almacenamiento de correos')
    
    # Agregar argumento para el puerto del servidor SMTP
    parser.add_argument('-p', '--port', type=int, required=True,
                        help='Puerto donde se ejecutará el servidor')

    # Parsear y retornar los argumentos
    return parser.parse_args()


@implementer(smtp.IMessage)
class ConsoleMessage:
    """
    Implementación de la interfaz IMessage para manejar mensajes SMTP.
    
    Esta clase recoge las líneas del mensaje entrante, extrae información 
    relevante y guarda el mensaje en el sistema de archivos.
    """
    def __init__(self, mail_storage, sender=None, recipient=None):
        self.lines = []  # Almacena las líneas del mensaje
        self.mail_storage = mail_storage

        # Convertir sender a cadena si es un objeto Address o tiene atributo 'dest'
        if isinstance(sender, smtp.Address):
            self.sender = str(sender)
        elif hasattr(sender, 'dest'):
            self.sender = str(sender.dest)
        else:
            self.sender = sender

        # Convertir recipient a cadena de forma similar
        if isinstance(recipient, smtp.Address):
            self.recipient = str(recipient)
        elif hasattr(recipient, 'dest'):
            self.recipient = str(recipient.dest)
        else:
            self.recipient = recipient

    def lineReceived(self, line):
        """
        Se invoca cada vez que se recibe una línea del mensaje SMTP.
        Decodifica la línea (si es bytes) y la almacena.
        Además, extrae encabezados 'From' y 'To' para actualizar remitente y destinatario.
        """
        if isinstance(line, bytes):
            line = line.decode("utf-8")
        self.lines.append(line)

        # Actualiza el remitente si se encuentra el encabezado "From:"
        if line.startswith("From:"):
            self.sender = line.split(":", 1)[1].strip()
        # Actualiza el destinatario si se encuentra el encabezado "To:"
        if line.startswith("To:"):
            self.recipient = line.split(":", 1)[1].strip()

    def eomReceived(self):
        """
        Se invoca cuando se recibe el fin del mensaje.
        Imprime el mensaje, guarda el correo en disco y retorna una confirmación.
        """
        print("Nuevo mensaje recibido:")
        print("\n".join(self.lines))
    
        # Si falta remitente o destinatario, se indica error pero se confirma recepción
        if not self.sender or not self.recipient:
            print("❌ No se puede guardar el mensaje: falta remitente o destinatario.")
            return defer.succeed('250 OK')
    
        # Extraer nombre de usuario y dominio del destinatario
        recipient_name, recipient_domain = self.recipient.split('@')
    
        # Crear la estructura de carpetas: ./<mail_storage>/<dominio>/<usuario>
        domain_folder = os.path.join(self.mail_storage, recipient_domain)
        user_folder = os.path.join(domain_folder, recipient_name)
    
        # Crear las carpetas si no existen
        os.makedirs(user_folder, exist_ok=True)
    
        # Generar un nombre de archivo único usando timestamp y el remitente
        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        file_name = f"{timestamp}_{self.sender.replace('@', '_').replace('.', '_')}.eml"
        file_path = os.path.join(user_folder, file_name)
    
        # Procesar el mensaje como MIME para manejar adjuntos correctamente
        raw_message = "\n".join(self.lines).encode("utf-8")
        message = BytesParser().parsebytes(raw_message)
    
        # Escribir el mensaje en formato de cadena en el archivo
        with open(file_path, 'w') as f:
            f.write(message.as_string())
    
        print(f"✅ Mensaje guardado en: {file_path}")
    
        # Limpiar los datos internos para liberar recursos
        self.lines = None
        self.sender = None
        self.recipient = None
    
        # Confirmar la recepción exitosa del mensaje
        return defer.succeed('250 OK')

    def connectionLost(self):
        """
        Se invoca cuando se pierde la conexión.
        Realiza la limpieza de datos internos.
        """
        self.lines = None


@implementer(smtp.IMessageDelivery)
class ConsoleMessageDelivery:
    """
    Implementación de la interfaz IMessageDelivery para la recepción de mensajes SMTP.
    
    Valida remitentes y destinatarios, y genera instancias de ConsoleMessage para manejar
    el almacenamiento de los mensajes.
    """
    def __init__(self, accepted_domains, mail_storage):
        self.accepted_domains = accepted_domains
        self.mail_storage = mail_storage
        os.makedirs(self.mail_storage, exist_ok=True)
        self.sender = None

    def receivedHeader(self, helo, origin, recipients):
        """
        Se invoca para generar un encabezado Received para el mensaje.
        Aquí se muestra información básica y se retorna None para usar el valor por defecto.
        """
        print(f"Received: ConsoleMessageDelivery from {origin}")
        return None

    def validateFrom(self, helo, origin):
        """
        Valida el remitente del mensaje.
        Registra y retorna el origen si es aceptable.
        """
        print(f"📥 Remitente aceptado: {origin}")
        self.sender = origin
        return origin

    def validateTo(self, user):
        """
        Valida el destinatario.
        Solo se aceptan destinatarios cuyo dominio esté en la lista de dominios aceptados.
        Si es válido, retorna una función que crea un ConsoleMessage.
        De lo contrario, lanza una excepción SMTPBadRcpt.
        """
        domain = user.dest.domain.decode('utf-8')
        if domain in self.accepted_domains:
            print(f"📥 Destinatario aceptado: {user.dest}")
            return lambda: ConsoleMessage(self.mail_storage, self.sender, user.dest)
        raise smtp.SMTPBadRcpt(user)


class ConsoleSMTPFactory(smtp.SMTPFactory):
    """
    Fábrica de protocolos SMTP que configura el manejo de mensajes y autenticación.
    
    Utiliza ConsoleMessageDelivery para el procesamiento de mensajes entrantes y
    configura los mecanismos de autenticación LOGIN y PLAIN.
    """
    protocol = smtp.ESMTP

    def __init__(self, portal, accepted_domains, mail_storage):
        smtp.SMTPFactory.__init__(self)
        self.delivery = ConsoleMessageDelivery(accepted_domains, mail_storage)
        self.portal = portal

    def buildProtocol(self, addr):
        """
        Construye y configura una instancia del protocolo SMTP.
        Asigna la instancia de delivery y configura los challengers de autenticación.
        """
        p = smtp.SMTPFactory.buildProtocol(self, addr)
        p.delivery = self.delivery
        p.challengers = {b"LOGIN": LOGINCredentials, b"PLAIN": PLAINCredentials}
        return p


@implementer(IRealm)
class SimpleRealm:
    """
    Implementación simple de un Realm para Twisted Cred.
    
    Permite solicitar un avatar que implementa IMessageDelivery para el manejo de mensajes.
    """
    def __init__(self, accepted_domains, mail_storage):
        self.accepted_domains = accepted_domains
        self.mail_storage = mail_storage

    def requestAvatar(self, avatarId, mind, *interfaces):
        """
        Retorna un avatar que implementa IMessageDelivery si se solicita esa interfaz.
        De lo contrario, lanza NotImplementedError.
        """
        if smtp.IMessageDelivery in interfaces:
            return smtp.IMessageDelivery, ConsoleMessageDelivery(self.accepted_domains, self.mail_storage), lambda: None
        raise NotImplementedError()


def main():
    """
    Función principal que configura y ejecuta el servidor SMTP.
    
    Procesa los argumentos de línea de comandos, configura el almacenamiento de correos,
    crea el portal de autenticación y arranca el reactor de Twisted para escuchar conexiones.
    """
    args = parse_arguments()

    # Lista de dominios permitidos (separados por coma)
    accepted_domains = args.domains.split(',')

    # Convertir la ruta de almacenamiento a una ruta absoluta
    mail_storage = os.path.abspath(args.mail_storage)
    
    # Crear el portal para autenticación con un realm simple
    portal = Portal(SimpleRealm(accepted_domains, mail_storage))
    # Usar un checker en memoria para autenticación (no usar en producción)
    checker = InMemoryUsernamePasswordDatabaseDontUse()
    checker.addUser("guest", "password")
    portal.registerChecker(checker)

    # Crear la fábrica SMTP con la configuración establecida
    factory = ConsoleSMTPFactory(portal, accepted_domains, mail_storage)

    print(f"Iniciando el servidor SMTP en el puerto {args.port}")
    reactor.listenTCP(args.port, factory)
    reactor.run()


if __name__ == "__main__":
    main()
