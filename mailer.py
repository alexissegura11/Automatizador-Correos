
#!/usr/bin/env python3
"""Envía correos personalizados a contactos de un archivo CSV."""

import argparse
import csv
import os
import smtplib
import ssl
import sys

from datetime import datetime
from email.message import EmailMessage
from email.utils import formataddr
from pathlib import Path

from dotenv import load_dotenv
from email_validator import validate_email, EmailNotValidError


# Cargamos las variables de configuración del archivo .env.
load_dotenv(Path(__file__).with_name(".env"))

# Aquí definimos las columnas que tendrá el historial.
HISTORY_FIELDS = [
    "timestamp", "status", "email", "name", "subject", "error"
]


# ---------------------------------------------------------
# VALIDACIÓN DE CORREOS
# ---------------------------------------------------------

def verificar_correo(email: str) -> tuple[bool, str, str]:
    """Valida el formato y consulta la configuración DNS del dominio."""

    # Quitamos espacios al principio y al final.
    email = email.strip()

    # Comprobamos que el campo no esté vacío.
    if not email:
        return False, "", "El correo está vacío."

    try:
        # Validamos la sintaxis y comprobamos la capacidad
        # del dominio para recibir correo mediante consultas DNS.
        resultado = validate_email(
            email,
            check_deliverability=True,
            allow_display_name=False,
            allow_smtputf8=False,
            strict=True,
            timeout=5
        )

        # Guardamos la dirección normalizada.
        return (
            True,
            resultado.normalized,
            "Formato correcto y dominio apto según DNS."
        )

    except EmailNotValidError as error:
        # El formato es incorrecto o el dominio no supera
        # la comprobación de disponibilidad de correo.
        return False, email, str(error)


# ---------------------------------------------------------
# PLANTILLAS PERSONALIZADAS
# ---------------------------------------------------------

def render_template(template: str, values: dict[str, str]) -> str:
    """Reemplaza {columnas} por los valores del contacto."""

    import re

    normalized_values = {
        key.strip().lower(): value
        for key, value in values.items()
    }

    pattern = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")

    return pattern.sub(
        lambda match: normalized_values.get(
            match.group(1).lower(), match.group(0)
        ),
        template
    )


# ---------------------------------------------------------
# CARGA DE CONTACTOS
# ---------------------------------------------------------

def load_contacts(csv_path: Path) -> list[dict[str, str]]:
    """Lee los contactos desde un archivo CSV."""

    if not csv_path.is_file():
        raise FileNotFoundError(
            f"No se encontró el archivo de contactos: {csv_path}"
        )

    with csv_path.open(
        "r", encoding="utf-8-sig", newline=""
    ) as contacts_file:

        reader = csv.DictReader(contacts_file)

        # Comprobamos que exista una columna llamada email.
        if not reader.fieldnames or not any(
            field and field.strip().lower() == "email"
            for field in reader.fieldnames
        ):
            raise ValueError(
                "El archivo CSV debe contener una columna 'email'."
            )

        contacts = [
            {
                key: value or ""
                for key, value in row.items()
                if key is not None
            }
            for row in reader
        ]

    if not contacts:
        raise ValueError("El archivo CSV no contiene contactos.")

    return contacts


def get_recipient_name(row: dict[str, str]) -> str:
    """Obtiene el nombre del destinatario si está disponible."""

    for key, value in row.items():
        if (
            key.strip().lower()
            in ("name", "first_name", "firstname")
            and value.strip()
        ):
            return value.strip()

    return ""


# ---------------------------------------------------------
# HISTORIAL DE ENVÍOS
# ---------------------------------------------------------

def append_history(
    history_path: Path,
    status: str,
    email: str,
    name: str,
    subject: str,
    error: str = ""
) -> None:
    """Guarda el resultado de cada intento en un CSV."""

    try:
        # Creamos la cabecera si el archivo todavía no existe.
        is_new = (
            not history_path.is_file()
            or history_path.stat().st_size == 0
        )

        with history_path.open(
            "a", encoding="utf-8", newline=""
        ) as history_file:

            writer = csv.DictWriter(
                history_file,
                fieldnames=HISTORY_FIELDS
            )

            if is_new:
                writer.writeheader()

            writer.writerow({
                "timestamp": datetime.now().isoformat(
                    timespec="seconds"
                ),
                "status": status,
                "email": email,
                "name": name,
                "subject": subject,
                "error": error
            })

    except OSError as error_:
        print(
            f"[WARN] No se pudo guardar el historial: {error_}",
            file=sys.stderr
        )


def read_history(history_path: Path) -> list[dict[str, str]]:
    """Lee el historial de envíos anterior."""

    if not history_path.is_file():
        return []

    with history_path.open(
        "r", encoding="utf-8", newline=""
    ) as history_file:
        return list(csv.DictReader(history_file))


def already_sent(
    history: list[dict[str, str]]
) -> set[tuple[str, str]]:
    """Obtiene los correos enviados correctamente y sus asuntos."""

    return {
        (
            entry.get("email", "").lower(),
            entry.get("subject", "")
        )
        for entry in history
        if entry.get("status") == "SENT"
    }


def show_history(history_path: Path, limit: int) -> int:
    """Muestra los últimos resultados del historial."""

    history = read_history(history_path)

    if not history:
        print(f"No hay historial disponible en {history_path}.")
        return 0

    entries = history[-limit:] if limit > 0 else history

    print(
        f"Historial ({len(entries)} de {len(history)} registros)"
    )

    for entry in entries:
        print(
            f"{entry.get('timestamp', '')} | "
            f"{entry.get('status', ''):<8} | "
            f"{entry.get('email', '')} | "
            f"{entry.get('subject', '')}"
        )

        if entry.get("error"):
            print(f"  Motivo: {entry['error']}")

    sent = sum(
        1 for entry in history
        if entry.get("status") == "SENT"
    )
    failed = sum(
        1 for entry in history
        if entry.get("status") == "FAILED"
    )
    skipped = sum(
        1 for entry in history
        if entry.get("status") == "SKIPPED"
    )

    print(
        f"Total: {sent} enviados, "
        f"{failed} fallidos, {skipped} omitidos."
    )

    return 0


# ---------------------------------------------------------
# PROGRAMA PRINCIPAL
# ---------------------------------------------------------

def main() -> int:

    # Aquí configuramos las opciones que podemos usar
    # al ejecutar el programa desde la terminal.
    parser = argparse.ArgumentParser(
        description="Envía correos personalizados desde un CSV."
    )

    parser.add_argument(
        "--csv",
        type=Path,
        default=Path(__file__).with_name("contacts.csv"),
        help="Archivo CSV con los contactos."
    )

    parser.add_argument(
        "--subject",
        default=os.getenv("EMAIL_SUBJECT"),
        help="Asunto del mensaje."
    )

    parser.add_argument(
        "--message",
        default=os.getenv("EMAIL_MESSAGE"),
        help="Contenido del mensaje."
    )

    parser.add_argument(
        "--history",
        type=Path,
        default=Path(
            os.getenv(
                "EMAIL_HISTORY",
                str(Path(__file__).with_name("email_history.csv"))
            )
        ),
        help="Archivo donde se guardan los resultados."
    )

    parser.add_argument(
        "--show-history",
        action="store_true",
        help="Muestra el historial y termina."
    )

    parser.add_argument(
        "--limit",
        type=int,
        default=20,
        help="Cantidad de registros del historial (0 = todos)."
    )

    parser.add_argument(
        "--skip-sent",
        action="store_true",
        help="Omite correos enviados antes con el mismo asunto."
    )

    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Prueba la lista sin enviar correos."
    )

    args = parser.parse_args()

    # Si solo queremos consultar el historial, terminamos aquí.
    if args.show_history:
        return show_history(args.history, args.limit)

    # Comprobamos que el asunto y el mensaje estén configurados.
    if not args.subject or not args.message:
        parser.error(
            "Configura EMAIL_SUBJECT y EMAIL_MESSAGE en .env "
            "o utiliza --subject y --message."
        )

    # Cargamos los contactos desde el archivo CSV.
    try:
        contacts = load_contacts(args.csv)
    except (OSError, ValueError, csv.Error) as error:
        print(f"[ERROR] {error}", file=sys.stderr)
        return 1

    # Leemos los datos del remitente y del servidor SMTP.
    sender_email = (
        os.getenv("SENDER_EMAIL")
        or os.getenv("EMAIL", "")
    )
    sender_password = (
        os.getenv("SENDER_PASSWORD")
        or os.getenv("PASSWORD", "")
    )
    sender_name = os.getenv("SENDER_NAME", "")
    smtp_server = os.getenv("SMTP_SERVER", "smtp.gmail.com")

    # Comprobamos que el puerto SMTP sea válido.
    try:
        smtp_port = int(os.getenv("SMTP_PORT", "587"))
    except ValueError:
        print("[ERROR] SMTP_PORT debe ser un número.", file=sys.stderr)
        return 1

    if not 1 <= smtp_port <= 65535:
        print("[ERROR] El puerto SMTP no es válido.", file=sys.stderr)
        return 1

    use_tls = (
        os.getenv("SMTP_USE_TLS", "true")
        .strip()
        .lower() in ("true", "1", "yes")
    )

    # Leemos el historial si queremos evitar mensajes duplicados.
    try:
        previously_sent = (
            already_sent(read_history(args.history))
            if args.skip_sent else set()
        )
    except (OSError, csv.Error) as error:
        print(
            f"[ERROR] No se pudo leer el historial: {error}",
            file=sys.stderr
        )
        return 1

    valid_contacts = []

    # Recorremos todos los contactos y verificamos sus correos.
    for row in contacts:

        # Buscamos la columna email sin importar las mayúsculas.
        email_key = next(
            (
                key for key in row
                if key.strip().lower() == "email"
            ),
            None
        )

        recipient_email = (
            row.get(email_key, "").strip()
            if email_key else ""
        )

        # Comprobamos el formato y el dominio mediante DNS.
        es_valido, correo, motivo = verificar_correo(
            recipient_email
        )

        if not es_valido:
            print(
                f"[SKIP] Correo no validado: "
                f"{recipient_email!r} - {motivo}"
            )

            # Guardamos el motivo por el cual se omitió.
            if not args.dry_run:
                append_history(
                    args.history,
                    "SKIPPED",
                    recipient_email,
                    get_recipient_name(row),
                    "",
                    motivo
                )

            # Pasamos al siguiente contacto sin enviar el mensaje.
            continue

        # Actualizamos el correo con su versión normalizada.
        row["email"] = correo

        print(f"[VALID] {correo} - {motivo}")

        # Preparamos el asunto personalizado del mensaje.
        subject = render_template(args.subject, row)

        # Si ya se envió el mismo asunto, lo omitimos.
        if (
            correo.casefold(),
            subject
        ) in previously_sent:
            print(f"[SKIP] Ya enviado anteriormente: {correo}")
            continue

        # Agregamos el contacto a la lista de destinatarios.
        valid_contacts.append(row)

    # Si no quedó ningún destinatario válido, terminamos.
    if not valid_contacts:
        print(
            "[ERROR] No hay destinatarios que hayan superado "
            "la validación.",
            file=sys.stderr
        )
        return 1

    # En modo de prueba solo mostramos los mensajes previstos.
    if args.dry_run:
        print(
            f"\n[DRY-RUN] Se prepararían "
            f"{len(valid_contacts)} correo(s):"
        )

        for row in valid_contacts:
            print(
                f" {row['email']} | "
                f"{render_template(args.subject, row)}"
            )

        print("\nNo se enviaron correos.")
        return 0

    # Antes del envío real comprobamos las credenciales.
    if not sender_email or not sender_password:
        print(
            "[ERROR] Configura SENDER_EMAIL y SENDER_PASSWORD "
            "en el archivo .env.",
            file=sys.stderr
        )
        return 1

    # Creamos el contexto SSL para proteger la conexión.
    context = ssl.create_default_context()

    sent_count = 0
    failed_count = 0

    try:
        # El puerto 465 utiliza SSL desde el inicio.
        if smtp_port == 465:
            smtp_connection = smtplib.SMTP_SSL(
                smtp_server,
                smtp_port,
                timeout=30,
                context=context
            )
        else:
            smtp_connection = smtplib.SMTP(
                smtp_server,
                smtp_port,
                timeout=30
            )

        with smtp_connection as smtp:
            smtp.ehlo()

            # En otros puertos activamos STARTTLS si está habilitado.
            if use_tls and smtp_port != 465:
                smtp.starttls(context=context)
                smtp.ehlo()

            # Iniciamos sesión en el servidor de correo.
            smtp.login(sender_email, sender_password)

            # Recorremos los destinatarios ya validados.
            for row in valid_contacts:

                recipient_email = row["email"]
                recipient_name = get_recipient_name(row)
                display_name = recipient_name or recipient_email

                subject = render_template(args.subject, row)
                body = render_template(args.message, row)

                # Construimos el mensaje personalizado.
                message = EmailMessage()
                message["Subject"] = subject

                message["From"] = (
                    formataddr((sender_name, sender_email))
                    if sender_name else sender_email
                )

                message["To"] = recipient_email
                message.set_content(body)

                try:
                    # Intentamos enviar el correo.
                    smtp.send_message(message)

                except smtplib.SMTPRecipientsRefused as error:
                    # El servidor rechazó al destinatario.
                    failed_count += 1

                    append_history(
                        args.history,
                        "FAILED",
                        recipient_email,
                        recipient_name,
                        subject,
                        str(error)
                    )

                    print(
                        f"[FAILED] {display_name} "
                        f"<{recipient_email}>: {error}"
                    )
                    continue

                except smtplib.SMTPDataError as error:
                    # El servidor rechazó el contenido del mensaje.
                    failed_count += 1

                    append_history(
                        args.history,
                        "FAILED",
                        recipient_email,
                        recipient_name,
                        subject,
                        str(error)
                    )

                    print(
                        f"[FAILED] {display_name} "
                        f"<{recipient_email}>: {error}"
                    )
                    continue

                # Registramos el envío aceptado por el servidor.
                sent_count += 1

                append_history(
                    args.history,
                    "SENT",
                    recipient_email,
                    recipient_name,
                    subject
                )

                print(
                    f"[SENT] {display_name} "
                    f"<{recipient_email}>"
                )

    except (OSError, smtplib.SMTPException) as error:
        print(
            f"[ERROR] Fallo SMTP después de "
            f"{sent_count} envío(s): {error}",
            file=sys.stderr
        )
        return 1

    # Mostramos el resumen final del proceso.
    print(
        f"\nProceso finalizado: {sent_count} enviado(s), "
        f"{failed_count} fallido(s)."
    )
    print(f"Historial guardado en: {args.history}")

    return 0


# Aquí iniciamos el programa cuando ejecutamos este archivo.
if __name__ == "__main__":
    raise SystemExit(main())
