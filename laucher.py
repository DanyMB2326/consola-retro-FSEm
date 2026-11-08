#!/usr/bin/env python3
"""Lanzador de consola retro: menu de ROMS controlado solo con gamepad.

Muestra las ROMS de ~/roms/{nes,snes,gba}, lanza Mednafen con la elegida y
vuelve al menu al salir del juego (Select + Start en el gamepad).

Autor: Daniela Martinez Bravo
Licencia: MIT
"""
import glob
import hashlib
import os
import re
import shutil
import signal
import struct
import subprocess
import time
import traceback

LOG = os.path.expanduser("~/launcher.log")


def registrar(texto):
    """Escribe una linea en ~/launcher.log (sirve para diagnosticar fallos)."""
    try:
        with open(LOG, "a") as f:
            f.write(time.strftime("%H:%M:%S ") + texto + "\n")
    except OSError:
        pass


# Video directo a la pantalla (sin escritorio). Debe ir antes de importar pygame.
os.environ.setdefault("SDL_VIDEODRIVER", "kmsdrm")
os.environ.setdefault("SDL_AUDIODRIVER", "alsa")  # audio directo, sin servidor de sonido
os.environ.setdefault("AUDIODEV", "plughw:vc4hdmi,0")  # salida HDMI, con conversion de formato
import pygame  # noqa: E402

ROMS_DIR = os.path.expanduser("~/roms")
SISTEMAS = {"nes": (".nes",), "snes": (".sfc", ".smc"), "gba": (".gba",)}
JS_DEV = "/dev/input/js0"
USB_MOUNT = "/mnt/usb"

# Numeracion de botones (la misma que muestra jstest para el GameSir K1)
BTN_A = 0
BTN_SELECT = 10
BTN_START = 11

FONDO = (10, 12, 40)
TEXTO = (220, 220, 235)
SELECCION = (255, 210, 60)
SUBTEXTO = (140, 145, 180)


def buscar_roms():
    """Devuelve una lista de (sistema, titulo, ruta) ordenada por sistema y nombre."""
    roms = []
    for sistema, extensiones in SISTEMAS.items():
        carpeta = os.path.join(ROMS_DIR, sistema)
        if not os.path.isdir(carpeta):
            continue
        for nombre in sorted(os.listdir(carpeta), key=str.lower):
            if nombre.lower().endswith(extensiones):
                titulo = os.path.splitext(nombre)[0].replace("_", " ")
                roms.append((sistema.upper(), titulo, os.path.join(carpeta, nombre)))
    return roms


def hay_usb():
    """True si hay una memoria USB (/dev/sdX1 o /dev/sdX) conectada."""
    return bool(glob.glob("/dev/sd?[0-9]") or glob.glob("/dev/sd?"))


def _md5(ruta):
    h = hashlib.md5()
    with open(ruta, "rb") as f:
        for bloque in iter(lambda: f.read(1 << 20), b""):
            h.update(bloque)
    return h.hexdigest()


def _nombre_limpio(nombre):
    base, ext = os.path.splitext(nombre)
    base = re.sub(r"[^A-Za-z0-9_.-]", "", base.replace(" ", "_"))
    return (base or "rom") + ext.lower()


def copiar_roms_usb():
    """Monta el USB (solo lectura), copia las ROMS nuevas y lo desmonta.

    Evita duplicados comparando el contenido (MD5), no solo el nombre.
    Devuelve el numero de ROMS copiadas, o None si no se pudo leer el USB.
    """
    dispositivos = sorted(glob.glob("/dev/sd?[0-9]")) or sorted(glob.glob("/dev/sd?"))
    if not dispositivos:
        return None
    time.sleep(1.0)  # deja que el sistema termine de reconocer el dispositivo
    montado = subprocess.run(
        ["sudo", "-n", "mount", "-o", "ro", dispositivos[0], USB_MOUNT],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0
    if not montado:
        return None

    copiadas = 0
    try:
        huellas = {_md5(ruta) for _, _, ruta in buscar_roms()}
        for raiz, _, archivos in os.walk(USB_MOUNT):
            for archivo in sorted(archivos):
                if archivo.startswith("."):
                    continue
                ext = os.path.splitext(archivo)[1].lower()
                sistema = next((s for s, e in SISTEMAS.items() if ext in e), None)
                if sistema is None:
                    continue
                origen = os.path.join(raiz, archivo)
                huella = _md5(origen)
                if huella in huellas:
                    continue  # esa ROM ya esta en la consola
                carpeta = os.path.join(ROMS_DIR, sistema)
                os.makedirs(carpeta, exist_ok=True)
                nombre = _nombre_limpio(archivo)
                destino = os.path.join(carpeta, nombre)
                n = 2
                while os.path.exists(destino):
                    base, e = os.path.splitext(nombre)
                    destino = os.path.join(carpeta, f"{base}_{n}{e}")
                    n += 1
                shutil.copy2(origen, destino)
                huellas.add(huella)
                copiadas += 1
    except OSError:
        pass
    finally:
        subprocess.run(["sudo", "-n", "umount", USB_MOUNT],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return copiadas


def resultado_usb(copiadas):
    if copiadas is None:
        return "No se pudo leer el USB"
    if copiadas == 0:
        return "No hay ROMS nuevas en el USB"
    return f"Se copiaron {copiadas} ROMS nuevas"


def menu(mensaje=None):
    """Muestra el menu y devuelve la ruta de la ROM elegida.

    `mensaje` es un aviso opcional (por ejemplo, el resultado de copiar un USB).
    """
    roms = buscar_roms()
    pygame.font.init()
    pygame.joystick.init()
    # Hay que guardar el objeto: si se descarta, pygame cierra el control
    # y deja de enviar eventos.
    control = pygame.joystick.Joystick(0) if pygame.joystick.get_count() else None
    # Justo despues de un juego el video puede tardar un instante en quedar
    # libre, asi que se reintenta en vez de cerrar el programa.
    pantalla = None
    for intento in range(60):
        try:
            pygame.display.init()
            pantalla = pygame.display.set_mode((0, 0), pygame.FULLSCREEN)
            break
        except pygame.error as error:
            if intento in (0, 9, 29, 59):  # no llenar el registro
                registrar(f"Pantalla no disponible (intento {intento + 1}): {error}")
            pygame.display.quit()
            time.sleep(0.5)
    if pantalla is None:
        raise RuntimeError("No se pudo abrir la pantalla")
    pygame.mouse.set_visible(False)
    ancho, alto = pantalla.get_size()

    f_titulo = pygame.font.Font(None, alto // 9)
    f_item = pygame.font.Font(None, alto // 15)
    f_pie = pygame.font.Font(None, alto // 26)
    alto_fila = alto // 14
    filas = max(3, alto // alto_fila - 4)

    sel = 0
    eje_activo = False
    reloj = pygame.time.Clock()
    usb_previo = hay_usb()
    mensaje_hasta = time.time() + 4 if mensaje else 0
    cuadros = 0

    while True:
        cuadros += 1
        if cuadros % 30 == 0:  # revisa el USB una vez por segundo
            ahora = hay_usb()
            if ahora and not usb_previo:
                pantalla.fill(FONDO)
                aviso = f_item.render("Copiando ROMS del USB...", True, SELECCION)
                pantalla.blit(aviso, ((ancho - aviso.get_width()) // 2, alto // 2))
                pygame.display.flip()
                mensaje = resultado_usb(copiar_roms_usb())
                mensaje_hasta = time.time() + 4
                roms = buscar_roms()
                sel = 0
            usb_previo = ahora

        for e in pygame.event.get():
            if not roms:
                continue
            if e.type == pygame.JOYHATMOTION:
                if e.value[1] == 1:
                    sel = (sel - 1) % len(roms)
                elif e.value[1] == -1:
                    sel = (sel + 1) % len(roms)
            elif e.type == pygame.JOYAXISMOTION and e.axis == 1:
                if abs(e.value) > 0.6 and not eje_activo:
                    sel = (sel + (1 if e.value > 0 else -1)) % len(roms)
                    eje_activo = True
                elif abs(e.value) < 0.3:
                    eje_activo = False
            elif e.type == pygame.JOYBUTTONDOWN and e.button == BTN_A:
                return roms[sel][2]

        pantalla.fill(FONDO)
        titulo = f_titulo.render("CONSOLA RETRO", True, SELECCION)
        pantalla.blit(titulo, ((ancho - titulo.get_width()) // 2, alto_fila // 2))

        if not roms:
            msg = f_item.render("No hay ROMS en ~/roms", True, TEXTO)
            pantalla.blit(msg, ((ancho - msg.get_width()) // 2, alto // 2))
        else:
            inicio = max(0, min(sel - filas // 2, len(roms) - filas))
            y = alto_fila * 2
            for i in range(inicio, min(inicio + filas, len(roms))):
                sistema, nombre, _ = roms[i]
                activa = i == sel
                if activa:
                    pygame.draw.rect(
                        pantalla, (40, 45, 100),
                        (ancho // 12, y - 4, ancho * 10 // 12, alto_fila - 4),
                        border_radius=8)
                color = SELECCION if activa else TEXTO
                pantalla.blit(f_item.render(f"[{sistema}]", True, SUBTEXTO),
                              (ancho // 10, y))
                pantalla.blit(f_item.render(nombre[:40], True, color),
                              (ancho // 10 + ancho // 8, y))
                y += alto_fila

        if mensaje and time.time() < mensaje_hasta:
            texto = f_item.render(mensaje, True, SELECCION)
            caja = pygame.Rect(0, 0, texto.get_width() + 80, texto.get_height() + 50)
            caja.center = (ancho // 2, alto // 2)
            pygame.draw.rect(pantalla, (60, 65, 130), caja, border_radius=12)
            pantalla.blit(texto, texto.get_rect(center=caja.center))

        pie = f_pie.render(
            "Cruceta: elegir     A: jugar     En el juego: Select + Start para salir",
            True, SUBTEXTO)
        pantalla.blit(pie, ((ancho - pie.get_width()) // 2, alto - alto_fila))
        pygame.display.flip()
        reloj.tick(30)


def generar_sonido():
    """Jingle de 4 notas con onda cuadrada, generado en codigo (sin archivos)."""
    import array

    tasa = 48000  # el HDMI trabaja de forma nativa a 48 kHz estereo
    notas = [(392, 0.12), (523, 0.12), (659, 0.12), (784, 0.35)]  # Sol Do Mi Sol
    muestras = array.array("h")
    # Silencio inicial: el audio HDMI tarda un instante en activarse y se
    # comeria las primeras notas.
    muestras.extend([0] * int(tasa * 0.7) * 2)
    for frecuencia, duracion in notas:
        n = int(tasa * duracion)
        for i in range(n):
            fase = (i * frecuencia / tasa) % 1.0
            envolvente = 1.0 - 0.6 * i / n
            valor = int((9000 if fase < 0.5 else -9000) * envolvente)
            muestras.append(valor)  # canal izquierdo
            muestras.append(valor)  # canal derecho
    return pygame.mixer.Sound(buffer=muestras.tobytes())


def animacion_inicio():
    """Logo con fundido y sonido al encender. Usa ~/assets/logo.png si existe."""
    pygame.display.init()
    pygame.font.init()
    pantalla = pygame.display.set_mode((0, 0), pygame.FULLSCREEN)
    pygame.mouse.set_visible(False)
    ancho, alto = pantalla.get_size()

    logo = None
    ruta_logo = os.path.expanduser("~/assets/logo.png")
    if os.path.exists(ruta_logo):
        logo = pygame.image.load(ruta_logo).convert_alpha()
        escala = min(ancho * 0.6 / logo.get_width(), alto * 0.6 / logo.get_height())
        logo = pygame.transform.smoothscale(
            logo, (int(logo.get_width() * escala), int(logo.get_height() * escala)))
    f_grande = pygame.font.Font(None, alto // 3)
    f_chica = pygame.font.Font(None, alto // 12)

    sonido = None
    try:
        pygame.mixer.init(48000, -16, 2, 1024)
        sonido = generar_sonido()
    except pygame.error:
        pass  # sin audio el lanzador sigue funcionando

    def escalar(color, f):
        return tuple(int(c * f) for c in color)

    def dibujar(f):
        pantalla.fill(FONDO)
        if logo is not None:
            logo.set_alpha(int(255 * f))
            pantalla.blit(logo, ((ancho - logo.get_width()) // 2,
                                 (alto - logo.get_height()) // 2))
        else:
            t1 = f_grande.render("RETRO", True, escalar(SELECCION, f))
            t2 = f_chica.render("NES  -  SNES  -  GBA", True, escalar(TEXTO, f))
            pantalla.blit(t1, ((ancho - t1.get_width()) // 2, alto // 3))
            pantalla.blit(t2, ((ancho - t2.get_width()) // 2, alto // 3 + t1.get_height()))
        pygame.display.flip()

    reloj = pygame.time.Clock()
    if sonido:
        sonido.play()
    for paso in range(51):  # fundido de entrada (~1.7 s)
        dibujar(paso / 50)
        reloj.tick(30)
    pygame.time.wait(1200)
    for paso in range(15, -1, -1):  # fundido de salida (~0.5 s)
        dibujar(paso / 15)
        reloj.tick(30)
    if sonido:
        pygame.mixer.quit()


def jugar(ruta):
    """Lanza Mednafen y lo cierra si se pulsan Select y Start a la vez."""
    pygame.quit()  # libera la pantalla para el emulador
    time.sleep(0.5)  # deja que el sistema libere el video antes del emulador
    env = dict(os.environ, SDL_VIDEODRIVER="kmsdrm")
    registrar(f"Lanzando mednafen: {ruta}")
    salida = open(os.path.expanduser("~/mednafen_ultimo.log"), "w")
    inicio = time.time()
    # Debian instala Mednafen en /usr/games, que no esta en el PATH de un servicio.
    mednafen = (shutil.which("mednafen")
                or next((p for p in ("/usr/games/mednafen", "/usr/bin/mednafen")
                         if os.path.exists(p)), "mednafen"))
    proc = subprocess.Popen(
        [mednafen, "-video.driver", "softfb", ruta],
        env=env, stdout=salida, stderr=subprocess.STDOUT)

    try:
        fd = os.open(JS_DEV, os.O_RDONLY | os.O_NONBLOCK)
    except OSError:
        fd = None
    pulsados = set()
    usb_previo = hay_usb()
    mensaje = None
    ticks = 0

    while proc.poll() is None:
        if fd is not None:
            try:
                while True:
                    datos = os.read(fd, 8)
                    if len(datos) < 8:
                        break
                    _, valor, tipo, numero = struct.unpack("<IhBB", datos)
                    if (tipo & 0x7F) == 1:  # evento de boton
                        if valor:
                            pulsados.add(numero)
                        else:
                            pulsados.discard(numero)
            except BlockingIOError:
                pass
            if {BTN_SELECT, BTN_START} <= pulsados:
                proc.terminate()
                break
        ticks += 1
        if ticks % 50 == 0:  # revisa el USB una vez por segundo
            ahora = hay_usb()
            if ahora and not usb_previo:
                os.kill(proc.pid, signal.SIGSTOP)  # pausa la emulacion
                mensaje = resultado_usb(copiar_roms_usb())
                os.kill(proc.pid, signal.SIGCONT)
                proc.terminate()  # vuelve al menu con la lista actualizada
                break
            usb_previo = ahora
        time.sleep(0.02)

    try:
        proc.wait(timeout=3)
    except subprocess.TimeoutExpired:
        proc.kill()
    salida.close()
    if fd is not None:
        os.close(fd)
    registrar(f"mednafen termino con codigo {proc.returncode} "
              f"despues de {time.time() - inicio:.1f} s")
    if mensaje is None and proc.returncode not in (0, -15) and time.time() - inicio < 5:
        mensaje = "El juego no pudo iniciar (ver ~/mednafen_ultimo.log)"
    return mensaje


def main():
    registrar("--- inicio del lanzador ---")
    try:
        animacion_inicio()
    except Exception:
        registrar("Error en la animacion:\n" + traceback.format_exc())
    mensaje = None
    while True:
        try:
            ruta = menu(mensaje)
            mensaje = jugar(ruta)
        except Exception:
            # Un error no debe reiniciar todo (y repetir el logo): se registra
            # y se vuelve al menu.
            registrar("Error:\n" + traceback.format_exc())
            mensaje = "Ocurrio un error (ver ~/launcher.log)"
            pygame.quit()
            time.sleep(1.0)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pygame.quit()
