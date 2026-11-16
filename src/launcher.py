#!/usr/bin/env python3
"""BIT-BOY: consola retro (NES, SNES y GBA) con menu controlado solo con gamepad.

Muestra las ROMS de ~/roms/{nes,snes,gba}, lanza Mednafen con la elegida y
vuelve al menu al salir del juego (Select + Start en el gamepad). Al conectar
una memoria USB copia las ROMS nuevas.

Autores: Daniela Martinez | Judith Mora | Luis Hernández
Licencia: MIT
"""
import ctypes
import glob
import hashlib
import os
import re
import shutil
import signal
import struct
import subprocess
import sys
import time
import traceback

LOG =os.path.expanduser("~/launcher.log")


def registrar(texto_log):
    """Escribe una linea en ~/launcher.log (sirve para diagnosticar fallos)."""
    try:
        with open(LOG, "a") as f:
            f.write(time.strftime("%H:%M:%S ") + texto_log + "\n")
    except OSError:
        pass


proceso = None  # emulador en curso (para poder cerrarlo si algo falla)


def matar_emulador():
    """Si un error deja el emulador abierto, lo cierra para liberar la pantalla."""
    global proceso
    if proceso is not None and proceso.poll() is None:
        try:
            proceso.send_signal(signal.SIGCONT)  # por si quedo en pausa
        except OSError:
            pass
        proceso.kill()
        proceso.wait()
    proceso = None


# Video directo a la pantalla (sin escritorio). Debe ir antes de importar pygame.
os.environ.setdefault("SDL_NO_SIGNAL_HANDLERS", "1")  # SDL no debe capturar SIGTERM/SIGINT
os.environ.setdefault("SDL_VIDEODRIVER", "kmsdrm")
os.environ.setdefault("SDL_AUDIODRIVER", "alsa")  # audio directo, sin servidor de sonido
os.environ.setdefault("AUDIODEV", "plughw:vc4hdmi,0")  # salida HDMI, con conversion de formato
import pygame  # noqa: E402

# Al cerrarse el emulador, la consola puede mandar SIGHUP; sin esto el lanzador
# moriria y el servicio lo reiniciaria (volviendo a mostrar el logo).
signal.signal(signal.SIGHUP, lambda *_: registrar("SIGHUP ignorada"))


def _terminar(*_):
    """Apagado limpio (systemctl stop/restart): cierra el emulador y sale."""
    registrar("Recibida SIGTERM: cerrando")
    matar_emulador()
    sys.exit(0)


signal.signal(signal.SIGTERM, _terminar)

MARCA_INTRO = "/tmp/bitboy_intro_hecho"  # /tmp se borra en cada encendido

ROMS_DIR =os.path.expanduser("~/roms")
ASSETS_DIR = os.path.expanduser("~/assets")
SISTEMAS = {"nes": (".nes",), "snes": (".sfc", ".smc"), "gba": (".gba",)}
JS_DEV = "/dev/input/js0"
USB_MOUNT = "/mnt/usb"

# Numeracion de botones (la misma que muestra jstest para el GameSir K1)
BTN_A = 0
BTN_B = 1
BTN_LB = 6
BTN_RB = 7
BTN_SELECT = 10
BTN_START = 11

# Paleta "Synthwave"
FONDO = (26, 16, 47)         # #1A102F  morado profundo
ACENTO = (59, 59, 115)       # #3B3B73  azul cobalto atenuado
SELECCION = (255, 42, 133)   # #FF2A85  magenta electrico
TEXTO = (0, 255, 255)        # #00FFFF  cyan neon
AMBAR = (255, 176, 0)        # luces del logo
VIOLETA = (160, 110, 255)


def mezclar(a, b, t):
    """Mezcla dos colores: t=0 da `a`, t=1 da `b`."""
    return tuple(int(a[i] * (1 - t) + b[i] * t) for i in range(3))


TEXTO_SUAVE = mezclar(FONDO, TEXTO, 0.62)
ACENTO_OSC = mezclar(FONDO, ACENTO, 0.55)
PANEL = mezclar(FONDO, ACENTO, 0.30)

SISTEMAS_INFO = {
    "NES": {"nombre": "NINTENDO NES", "bits": "8 BITS", "color": AMBAR},
    "SNES": {"nombre": "SUPER NINTENDO", "bits": "16 BITS", "color": TEXTO},
    "GBA": {"nombre": "GAME BOY ADVANCE", "bits": "32 BITS", "color": VIOLETA},
}
TABS = ["TODOS", "NES", "SNES", "GBA"]
OPCIONES_PODER = ["APAGAR", "REINICIAR", "CANCELAR"]

# El menu se dibuja en un lienzo de 960x540 y se escala a la pantalla
# (en 1080p es exactamente el doble, asi el pixel art se ve nitido).
ANCHO_V, ALTO_V = 960, 540
FILAS = 5


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


def titulo_corto(nombre):
    """Quita etiquetas como (J), (v1.2) o numeros de version del nombre."""
    t = re.sub(r"\s*[\(\[][^\)\]]*[\)\]]", "", nombre)
    t = re.sub(r"[-_\s]*v?\d+([.\-]\d+)+\s*$", "", t, flags=re.I)
    return t.strip() or nombre


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


def _sudo(args):
    """Ejecuta `sudo -n` y devuelve (codigo, mensaje de error)."""
    r = subprocess.run(["sudo", "-n"] + args, stdout=subprocess.PIPE,
                       stderr=subprocess.PIPE, text=True)
    return r.returncode, " ".join((r.stderr or "").split())


def verificar_permisos():
    """Al iniciar, comprueba que sudo no pida contrasena y que exista /mnt/usb."""
    problemas = []
    for cmd in (["/usr/bin/mount", "-o", "ro", "/dev/sda1", USB_MOUNT],
                ["/usr/bin/umount", USB_MOUNT],
                ["/usr/bin/systemctl", "poweroff"]):
        codigo, _ = _sudo(["-l"] + cmd)
        if codigo != 0:
            problemas.append(" ".join(cmd[:2]))
    if not os.path.isdir(USB_MOUNT):
        problemas.append(f"falta la carpeta {USB_MOUNT}")
    if problemas:
        registrar("AVISO permisos: " + "; ".join(problemas))
    else:
        registrar("Permisos de USB y apagado: OK")
    return not problemas


def _montar_usb(dispositivos):
    """Intenta montar cada particion (hasta 3 veces). Devuelve (ok, motivo)."""
    if os.path.ismount(USB_MOUNT):  # quedo montado de un intento anterior
        _sudo(["/usr/bin/umount", USB_MOUNT])
    motivo = "sin dispositivo"
    for intento in range(3):
        for disp in dispositivos:
            codigo, error = _sudo(["/usr/bin/mount", "-o", "ro", disp, USB_MOUNT])
            if codigo == 0:
                return True, ""
            motivo = f"{disp}: {error}"
        time.sleep(1.5)  # a veces el USB aun no esta listo
    return False, motivo


def copiar_roms_usb():
    """Monta el USB (solo lectura), copia las ROMS nuevas y lo desmonta.

    Evita duplicados comparando el contenido (MD5), no solo el nombre.
    Devuelve el numero de ROMS copiadas, o None si no se pudo leer el USB.
    """
    dispositivos = sorted(glob.glob("/dev/sd?[0-9]")) or sorted(glob.glob("/dev/sd?"))
    if not dispositivos:
        return None
    time.sleep(1.0)  # deja que el sistema termine de reconocer el dispositivo
    montado, motivo = _montar_usb(dispositivos)
    if not montado:
        registrar(f"USB: no se pudo montar ({motivo})")
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
    except OSError as e:
        registrar(f"USB: error al copiar ({e})")
    finally:
        _sudo(["/usr/bin/umount", USB_MOUNT])
    registrar(f"USB: {copiadas} ROMS copiadas")
    return copiadas


def resultado_usb(copiadas):
    if copiadas is None:
        return "No se pudo leer el USB"
    if copiadas == 0:
        return "No hay ROMS nuevas en el USB"
    return f"Se copiaron {copiadas} ROMS nuevas"


class Control:
    """Lee el gamepad directo de /dev/input/js0 y se reconecta solo.

    No usa SDL para el control: SDL no siempre detecta un gamepad que se vuelve
    a conectar mientras el programa corre como servicio.
    """

    def __init__(self):
        self.fd = None
        self.pulsados = set()
        self.ultimo_intento = 0.0
        self._abrir()

    def _abrir(self):
        try:
            self.fd = os.open(JS_DEV, os.O_RDONLY | os.O_NONBLOCK)
            registrar("Control conectado")
        except OSError:
            self.fd = None

    def cerrar(self):
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None
        self.pulsados.clear()

    def eventos(self):
        """Devuelve una lista de (tipo, numero, valor); tipo es 'boton' o 'eje'."""
        if self.fd is None:  # intenta recuperar el control cada segundo
            if time.time() - self.ultimo_intento < 1.0:
                return []
            self.ultimo_intento = time.time()
            self._abrir()
            if self.fd is None:
                return []
        salida = []
        try:
            while True:
                datos = os.read(self.fd, 8)
                if len(datos) < 8:
                    break
                _, valor, tipo, numero = struct.unpack("<IhBB", datos)
                inicial = bool(tipo & 0x80)  # estado inicial al abrir, no una pulsacion
                tipo &= 0x7F
                if tipo == 1:
                    if valor:
                        self.pulsados.add(numero)
                    else:
                        self.pulsados.discard(numero)
                if not inicial and tipo in (1, 2):
                    salida.append(("boton" if tipo == 1 else "eje", numero, valor))
        except BlockingIOError:
            pass
        except OSError:
            # El control se desconecto (por ejemplo, un bajon de voltaje).
            registrar("Control desconectado; esperando a que vuelva")
            self.cerrar()
        return salida


# ---------------------------------------------------------------------------
# Dibujo
# ---------------------------------------------------------------------------
_fuentes = {}
_cache_texto = {}


def _fuente(tam):
    if tam not in _fuentes:
        _fuentes[tam] = pygame.font.Font(None, tam)
    return _fuentes[tam]


def texto(cadena, tam, color, escala=1):
    """Texto estilo pixel: sin suavizado (y ampliado si escala > 1)."""
    clave = (cadena, tam, color, escala)
    s = _cache_texto.get(clave)
    if s is None:
        if len(_cache_texto) > 400:
            _cache_texto.clear()
        s = _fuente(tam).render(cadena, False, color)
        if escala != 1:
            s = pygame.transform.scale(s, (s.get_width() * escala, s.get_height() * escala))
        _cache_texto[clave] = s
    return s


def recortar(cadena, tam, ancho):
    """Acorta el texto con '...' para que quepa en `ancho` pixeles."""
    fuente = _fuente(tam)
    if fuente.size(cadena)[0] <= ancho:
        return cadena
    while cadena and fuente.size(cadena + "...")[0] > ancho:
        cadena = cadena[:-1]
    return cadena.rstrip() + "..."


def envolver(cadena, tam, ancho, max_lineas):
    """Parte el texto en lineas que caben en `ancho` (la ultima se recorta)."""
    fuente = _fuente(tam)
    lineas, actual = [], ""
    for palabra in cadena.split():
        prueba = (actual + " " + palabra).strip()
        if fuente.size(prueba)[0] <= ancho or not actual:
            actual = prueba
        else:
            lineas.append(actual)
            actual = palabra
    if actual:
        lineas.append(actual)
    if len(lineas) > max_lineas:
        lineas = lineas[:max_lineas]
        lineas[-1] = recortar(lineas[-1] + "...", tam, ancho)
    return [recortar(linea, tam, ancho) for linea in lineas]


def dibujar_icono(sup, cx, cy, tam, sistema):
    """Cartucho estilizado de cada consola, en su color."""
    info = SISTEMAS_INFO[sistema]
    color = info["color"]
    if sistema == "NES":
        w, h = tam * 0.78, tam
    elif sistema == "SNES":
        w, h = tam, tam * 0.82
    else:
        w, h = tam * 0.8, tam * 0.92
    r = pygame.Rect(0, 0, int(w), int(h))
    r.center = (cx, cy)
    pygame.draw.rect(sup, color, r, border_radius=max(3, tam // 10))
    for i in range(3):  # ranuras de agarre
        y = r.top + int(h * 0.10) + i * max(2, int(h * 0.07))
        pygame.draw.line(sup, FONDO, (r.left + int(w * 0.18), y),
                         (r.right - int(w * 0.18), y), max(1, tam // 28))
    et = pygame.Rect(0, 0, int(w * 0.66), int(h * 0.40))  # etiqueta con los bits
    et.center = (r.centerx, r.centery + int(h * 0.04))
    pygame.draw.rect(sup, FONDO, et, border_radius=max(2, tam // 16))
    t = texto(info["bits"].split()[0], max(14, tam // 2), color)
    sup.blit(t, t.get_rect(center=et.center))
    con = pygame.Rect(0, 0, int(w * 0.5), max(3, int(h * 0.10)))  # conector
    con.midbottom = (r.centerx, r.bottom - 2)
    pygame.draw.rect(sup, FONDO, con)


def dibujar_aviso(sup, mensaje):
    """Cuadro centrado con un aviso (por ejemplo, el resultado de copiar un USB)."""
    t = texto(mensaje.upper(), 26, TEXTO)
    caja = pygame.Rect(0, 0, t.get_width() + 70, t.get_height() + 44)
    caja.center = (ANCHO_V // 2, ALTO_V // 2)
    pygame.draw.rect(sup, FONDO, caja.inflate(10, 10), border_radius=16)
    pygame.draw.rect(sup, ACENTO, caja, border_radius=14)
    pygame.draw.rect(sup, SELECCION, caja, 3, border_radius=14)
    sup.blit(t, t.get_rect(center=caja.center))


def dibujar_dialogo_poder(sup, indice):
    """Cuadro para apagar o reiniciar; `indice` es la opcion resaltada."""
    velo = pygame.Surface((ANCHO_V, ALTO_V), pygame.SRCALPHA)
    velo.fill((10, 5, 20, 190))
    sup.blit(velo, (0, 0))
    caja = pygame.Rect(0, 0, 440, 300)
    caja.center = (ANCHO_V // 2, ALTO_V // 2)
    pygame.draw.rect(sup, PANEL, caja, border_radius=16)
    pygame.draw.rect(sup, TEXTO, caja, 2, border_radius=16)
    t = texto("APAGAR LA CONSOLA", 30, TEXTO)
    sup.blit(t, t.get_rect(midtop=(caja.centerx, caja.y + 18)))
    y = caja.y + 54
    for linea in envolver("APAGA SIEMPRE DESDE AQUI PARA CUIDAR LA TARJETA SD", 16,
                          caja.width - 60, 2):
        t = texto(linea, 16, TEXTO_SUAVE)
        sup.blit(t, t.get_rect(midtop=(caja.centerx, y)))
        y += 18
    for i, opcion in enumerate(OPCIONES_PODER):
        r = pygame.Rect(caja.x + 40, caja.y + 106 + i * 58, caja.width - 80, 46)
        activa = i == indice
        pygame.draw.rect(sup, ACENTO if activa else ACENTO_OSC, r, border_radius=12)
        if activa:
            pygame.draw.rect(sup, SELECCION, r, 3, border_radius=12)
        t = texto(opcion, 26, TEXTO if activa else TEXTO_SUAVE)
        sup.blit(t, t.get_rect(center=r.center))


def apagar_consola(pantalla, lienzo, accion):
    """Apaga o reinicia el sistema. Solo regresa (False) si no se pudo hacerlo."""
    apagando = accion == "APAGAR"
    lienzo.fill(FONDO)
    t = texto("APAGANDO..." if apagando else "REINICIANDO...", 28, TEXTO, 2)
    lienzo.blit(t, t.get_rect(center=(ANCHO_V // 2, ALTO_V // 2 - 20)))
    t = texto("ESPERA A QUE TERMINE ANTES DE DESCONECTAR LA CORRIENTE", 18, TEXTO_SUAVE)
    lienzo.blit(t, t.get_rect(center=(ANCHO_V // 2, ALTO_V // 2 + 50)))
    presentar(pantalla, lienzo)
    registrar(f"Solicitado: {accion}")
    resultado = subprocess.run(
        ["sudo", "-n", "/usr/bin/systemctl", "poweroff" if apagando else "reboot"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if resultado.returncode != 0:
        detalle = " ".join((resultado.stderr or resultado.stdout or "").split())
        registrar(f"No se pudo ejecutar {accion} (codigo {resultado.returncode}): {detalle}")
        return False
    time.sleep(60)  # el sistema ya esta cerrando los servicios
    return True


def dibujar_menu(lienzo, items, sel, tab, total, aviso, logo_chico, dialogo=None):
    """Dibuja el menu completo en el lienzo virtual de 960x540."""
    lienzo.fill(FONDO)

    # --- cabecera: logo, pestañas por consola y contador -------------------
    x = 24
    if logo_chico is not None:
        # BLEND_RGB_MAX: el fondo oscuro del logo desaparece contra el del menu
        lienzo.blit(logo_chico, (x, 10), special_flags=pygame.BLEND_RGB_MAX)
        x += logo_chico.get_width() + 30
    else:
        t = texto("BIT-BOY", 28, TEXTO, 2)
        lienzo.blit(t, (x, 14))
        x += t.get_width() + 30
    for i, nombre in enumerate(TABS):
        r = pygame.Rect(x, 24, 96, 34)
        activa = i == tab
        pygame.draw.rect(lienzo, ACENTO if activa else ACENTO_OSC, r, border_radius=8)
        if activa:
            pygame.draw.rect(lienzo, SELECCION, r, 3, border_radius=8)
            pygame.draw.polygon(lienzo, SELECCION, [
                (r.centerx - 7, r.bottom + 9), (r.centerx + 7, r.bottom + 9),
                (r.centerx, r.bottom + 2)])
        t = texto(nombre, 22, TEXTO if activa else TEXTO_SUAVE)
        lienzo.blit(t, t.get_rect(center=r.center))
        x += 104
    t = texto(f"{total} JUEGOS", 22, TEXTO_SUAVE)
    lienzo.blit(t, (ANCHO_V - 24 - t.get_width(), 33))

    # --- lista de juegos (izquierda) ---------------------------------------
    arriba = 84
    if not items:
        t = texto("NO HAY JUEGOS AQUI", 30, TEXTO)
        lienzo.blit(t, t.get_rect(center=(300, 250)))
        t = texto("CONECTA UNA MEMORIA USB CON ROMS", 20, TEXTO_SUAVE)
        lienzo.blit(t, t.get_rect(center=(300, 290)))
    else:
        inicio = max(0, min(sel - FILAS // 2, len(items) - FILAS))
        for n in range(inicio, min(inicio + FILAS, len(items))):
            sistema, nombre, _ = items[n]
            r = pygame.Rect(24, arriba + (n - inicio) * 74, 520, 66)
            activa = n == sel
            pygame.draw.rect(lienzo, ACENTO if activa else ACENTO_OSC, r, border_radius=10)
            if activa:
                pygame.draw.rect(lienzo, SELECCION, r, 3, border_radius=10)
            dibujar_icono(lienzo, r.x + 38, r.centery, 40, sistema)
            titulo = recortar(titulo_corto(nombre).upper(), 26, r.width - 72 - 16)
            lienzo.blit(texto(titulo, 26, TEXTO), (r.x + 72, r.y + 9))
            sub = f"{sistema}  -  {SISTEMAS_INFO[sistema]['bits']}"
            lienzo.blit(texto(sub, 18, TEXTO_SUAVE if not activa else TEXTO), (r.x + 72, r.y + 38))
        if len(items) > FILAS:  # barra de desplazamiento
            alto_pista = FILAS * 74 - 8
            pista = pygame.Rect(552, arriba, 6, alto_pista)
            pygame.draw.rect(lienzo, ACENTO_OSC, pista, border_radius=3)
            alto_pulgar = max(24, alto_pista * FILAS // len(items))
            frac = inicio / (len(items) - FILAS)
            pulgar = pygame.Rect(552, arriba + int((alto_pista - alto_pulgar) * frac),
                                 6, alto_pulgar)
            pygame.draw.rect(lienzo, SELECCION, pulgar, border_radius=3)

    # --- panel de detalle (derecha) ----------------------------------------
    p = pygame.Rect(576, arriba, 360, FILAS * 74 - 8)
    pygame.draw.rect(lienzo, PANEL, p, border_radius=14)
    pygame.draw.rect(lienzo, TEXTO, p, 2, border_radius=14)
    if items:
        sistema, nombre, ruta = items[sel]
        info = SISTEMAS_INFO[sistema]
        y = p.y + 16
        for linea in envolver(titulo_corto(nombre).upper(), 26, p.width - 40, 2):
            lienzo.blit(texto(linea, 26, TEXTO), (p.x + 20, y))
            y += 28
        dibujar_icono(lienzo, p.centerx, p.y + 128, 84, sistema)
        try:
            kb = os.path.getsize(ruta) / 1024
            tam = f"{kb / 1024:.1f} MB" if kb >= 1024 else f"{kb:.0f} KB"
        except OSError:
            tam = "-"
        y = p.y + 188
        for etiqueta, valor in (
                ("CONSOLA", info["nombre"]),
                ("ARCHIVO", os.path.basename(ruta)),
                ("TAMAÑO", tam),
                ("JUEGO", f"{sel + 1} DE {len(items)}")):
            lienzo.blit(texto(etiqueta, 16, TEXTO_SUAVE), (p.x + 20, y))
            lienzo.blit(texto(recortar(valor.upper(), 20, p.width - 40), 20, TEXTO),
                        (p.x + 20, y + 15))
            y += 40

    # --- pie con los controles ---------------------------------------------
    x, y = 24, 474
    for etiqueta, desc in (("A", "JUGAR"), ("LB / RB", "CONSOLA"), ("CRUCETA", "ELEGIR"),
                           ("START", "APAGAR"), ("SELECT + START", "SALIR DEL JUEGO")):
        e = texto(etiqueta, 18, TEXTO)
        r = pygame.Rect(x, y, e.get_width() + 20, 28)
        pygame.draw.rect(lienzo, ACENTO, r, border_radius=14)
        pygame.draw.rect(lienzo, SELECCION, r, 2, border_radius=14)
        lienzo.blit(e, e.get_rect(center=r.center))
        d = texto(desc, 18, TEXTO_SUAVE)
        lienzo.blit(d, d.get_rect(midleft=(r.right + 8, r.centery)))
        x = r.right + 8 + d.get_width() + 22

    if dialogo is not None:
        dibujar_dialogo_poder(lienzo, dialogo)
    elif aviso:
        dibujar_aviso(lienzo, aviso)


def presentar(pantalla, lienzo):
    """Escala el lienzo a la pantalla (centrado, sin deformar) y lo muestra."""
    w, h = pantalla.get_size()
    k = min(w / ANCHO_V, h / ALTO_V)
    tam = (int(ANCHO_V * k), int(ALTO_V * k))
    if tam == (w, h):
        pygame.transform.scale(lienzo, tam, pantalla)
    else:
        pantalla.fill(FONDO)
        pantalla.blit(pygame.transform.scale(lienzo, tam),
                      ((w - tam[0]) // 2, (h - tam[1]) // 2))
    volcar(pantalla)


VIRT = (1280, 720)   # tamano del lienzo; la GPU lo escala a la pantalla
_GLES = None         # presentador activo (None = se usa pygame.display.flip)


class _Gles:
    """Presenta un lienzo (Surface) con GLES2: lo sube como textura y lo dibuja a
    pantalla completa, con barras negras si la proporcion no coincide.

    El camino normal de pygame (superficie de ventana) deja la pantalla en negro con
    SDL + kmsdrm en la Raspberry Pi 3; un contexto GLES propio si funciona.
    """

    VERT = (b"attribute vec2 pos; attribute vec2 uv; varying vec2 v;"
            b"void main(){ v = uv; gl_Position = vec4(pos, 0.0, 1.0); }")
    FRAG = (b"precision mediump float; varying vec2 v; uniform sampler2D t;"
            b"void main(){ gl_FragColor = texture2D(t, v); }")

    def __init__(self, ventana, ancho, alto):
        c = ctypes
        gl = self.gl = c.CDLL("libGLESv2.so.2")
        P = c.c_void_p
        U, I = c.c_uint, c.c_int
        for nombre, res, args in (
            ("glCreateShader", U, [U]),
            ("glShaderSource", None, [U, I, c.POINTER(c.c_char_p), c.POINTER(I)]),
            ("glCompileShader", None, [U]),
            ("glGetShaderiv", None, [U, U, c.POINTER(I)]),
            ("glCreateProgram", U, []),
            ("glAttachShader", None, [U, U]),
            ("glBindAttribLocation", None, [U, U, c.c_char_p]),
            ("glLinkProgram", None, [U]),
            ("glUseProgram", None, [U]),
            ("glGetUniformLocation", I, [U, c.c_char_p]),
            ("glUniform1i", None, [I, I]),
            ("glGenTextures", None, [I, c.POINTER(U)]),
            ("glBindTexture", None, [U, U]),
            ("glTexParameteri", None, [U, U, I]),
            ("glPixelStorei", None, [U, I]),
            ("glTexImage2D", None, [U, I, I, I, I, I, U, U, P]),
            ("glTexSubImage2D", None, [U, I, I, I, I, I, U, U, P]),
            ("glEnableVertexAttribArray", None, [U]),
            ("glVertexAttribPointer", None, [U, I, U, c.c_ubyte, I, P]),
            ("glDrawArrays", None, [U, I, I]),
            ("glViewport", None, [I, I, I, I]),
            ("glClearColor", None, [c.c_float] * 4),
            ("glClear", None, [U]),
            ("glReadPixels", None, [I, I, I, I, U, U, P]),
        ):
            f = getattr(gl, nombre)
            f.restype, f.argtypes = res, args

        def compilar(tipo, fuente):
            sh = gl.glCreateShader(tipo)
            gl.glShaderSource(sh, 1, (c.c_char_p * 1)(fuente), None)
            gl.glCompileShader(sh)
            ok = I(0)
            gl.glGetShaderiv(sh, 0x8B81, c.byref(ok))  # GL_COMPILE_STATUS
            if not ok.value:
                raise RuntimeError("shader GLES no compilo")
            return sh

        prog = gl.glCreateProgram()
        gl.glAttachShader(prog, compilar(0x8B31, self.VERT))  # vertex
        gl.glAttachShader(prog, compilar(0x8B30, self.FRAG))  # fragment
        gl.glBindAttribLocation(prog, 0, b"pos")
        gl.glBindAttribLocation(prog, 1, b"uv")
        gl.glLinkProgram(prog)
        gl.glUseProgram(prog)
        gl.glUniform1i(gl.glGetUniformLocation(prog, b"t"), 0)

        # cuadrilatero a pantalla completa: (x, y, u, v) por vertice
        datos = (c.c_float * 16)(-1, -1, 0, 1,   1, -1, 1, 1,   -1, 1, 0, 0,   1, 1, 1, 0)
        self._datos = datos  # se conserva: GLES lee estos datos en cada dibujo
        base = c.cast(datos, c.c_void_p).value
        gl.glEnableVertexAttribArray(0)
        gl.glEnableVertexAttribArray(1)
        gl.glVertexAttribPointer(0, 2, 0x1406, 0, 16, c.c_void_p(base))
        gl.glVertexAttribPointer(1, 2, 0x1406, 0, 16, c.c_void_p(base + 8))

        tex = U(0)
        gl.glGenTextures(1, c.byref(tex))
        gl.glBindTexture(0x0DE1, tex.value)
        gl.glPixelStorei(0x0CF5, 1)                       # UNPACK_ALIGNMENT = 1
        for param, valor in ((0x2800, 0x2601), (0x2801, 0x2601),   # filtro lineal
                             (0x2802, 0x812F), (0x2803, 0x812F)):  # sin repetir bordes
            gl.glTexParameteri(0x0DE1, param, valor)
        gl.glTexImage2D(0x0DE1, 0, 0x1907, ancho, alto, 0, 0x1907, 0x1401, None)
        self.ancho, self.alto = ancho, alto
        self.ventana = ventana.get_size()

    def mostrar(self, lienzo):
        gl = self.gl
        W, H = self.ventana
        k = min(W / self.ancho, H / self.alto)
        w, h = int(self.ancho * k), int(self.alto * k)
        gl.glViewport(0, 0, W, H)
        gl.glClearColor(0.0, 0.0, 0.0, 1.0)
        gl.glClear(0x4000)
        gl.glViewport((W - w) // 2, (H - h) // 2, w, h)
        datos = pygame.image.tobytes(lienzo, "RGB")
        gl.glTexSubImage2D(0x0DE1, 0, 0, 0, self.ancho, self.alto, 0x1907, 0x1401, datos)
        gl.glDrawArrays(0x0005, 0, 4)  # TRIANGLE_STRIP
        pygame.display.flip()

    def leer(self, x, y):
        """Color RGB de un pixel de la pantalla (para pruebas)."""
        buf = (ctypes.c_ubyte * 4)()
        self.gl.glReadPixels(x, y, 1, 1, 0x1908, 0x1401, ctypes.cast(buf, ctypes.c_void_p))
        return tuple(buf[:3])


def volcar(sup):
    """Muestra el lienzo: por GLES si esta activo, o con el flip normal de pygame."""
    if _GLES is not None:
        _GLES.mostrar(sup)
    else:
        pygame.display.flip()


def _sin_cursor():
    """Oculta el puntero (la flecha) que SDL/kmsdrm dibuja al abrir la pantalla."""
    try:
        pygame.mouse.set_visible(False)
        # cursor de 8x8 totalmente transparente, por si el oculto no se respeta
        pygame.mouse.set_cursor((8, 8), (0, 0), (0,) * 8, (0,) * 8)
        pygame.mouse.set_visible(False)
    except (pygame.error, TypeError, ValueError):
        pass


def abrir_pantalla():
    """Abre la pantalla completa; reintenta si el video aun no esta libre."""
    for intento in range(60):
        try:
            pygame.display.init()
            pygame.mouse.set_visible(False)  # antes de abrir la pantalla
            # Sin canal alfa en el buffer: con Mesa/SDL en la Pi 3 un buffer ARGB deja
            # la pantalla en negro (comprobado con pruebas de colores).
            global _GLES
            _GLES = None
            if not os.environ.get("BITBOY_SIN_GLES"):
                try:
                    pygame.display.gl_set_attribute(pygame.GL_CONTEXT_MAJOR_VERSION, 2)
                    pygame.display.gl_set_attribute(pygame.GL_CONTEXT_MINOR_VERSION, 0)
                    pygame.display.gl_set_attribute(
                        pygame.GL_CONTEXT_PROFILE_MASK, pygame.GL_CONTEXT_PROFILE_ES)
                    ventana = pygame.display.set_mode((0, 0), pygame.FULLSCREEN | pygame.OPENGL)
                    _GLES = _Gles(ventana, *VIRT)
                    _sin_cursor()
                    return pygame.Surface(VIRT)  # lienzo; volcar() lo envia a la pantalla
                except Exception as error:  # sin GLES: camino normal de pygame
                    registrar(f"GLES no disponible ({error}); se usa pygame normal")
                    _GLES = None
                    pygame.display.quit()
                    pygame.display.init()
            ventana = pygame.display.set_mode((0, 0), pygame.FULLSCREEN)
            _sin_cursor()
            return ventana
        except pygame.error as error:
            if intento in (0, 9, 29, 59):  # no llenar el registro
                registrar(f"Pantalla no disponible (intento {intento + 1}): {error}")
            pygame.display.quit()
            time.sleep(0.5)
    raise RuntimeError("No se pudo abrir la pantalla")


def cargar_logo_chico():
    ruta = os.path.join(ASSETS_DIR, "logo_chico.png")
    try:
        img = pygame.image.load(ruta).convert()
        alto = 56
        return pygame.transform.smoothscale(
            img, (int(img.get_width() * alto / img.get_height()), alto))
    except (pygame.error, FileNotFoundError):
        return None


def filtrar(roms, tab):
    return roms if tab == 0 else [r for r in roms if r[0] == TABS[tab]]


def menu(mensaje=None):
    """Muestra el menu y devuelve la ruta de la ROM elegida.

    `mensaje` es un aviso opcional (por ejemplo, el resultado de copiar un USB).
    """
    roms = buscar_roms()
    pygame.font.init()
    control = Control()
    pantalla = abrir_pantalla()
    lienzo = pygame.Surface((ANCHO_V, ALTO_V), 0, pantalla)
    logo_chico = cargar_logo_chico()

    tab, sel = 0, 0
    items = filtrar(roms, tab)
    reloj = pygame.time.Clock()
    usb_previo = hay_usb()
    aviso_hasta = time.time() + 4 if mensaje else 0
    aviso = mensaje
    aviso_mostrado = None
    dialogo = None  # None, o la opcion resaltada del cuadro de apagado
    cuadros = 0
    sucio = True  # solo se redibuja cuando algo cambia
    dir_hat = dir_palanca = 0
    t_dir = t_rep = 0.0

    def mover(d):
        nonlocal sel, sucio
        if items:
            sel = (sel + d) % len(items)
            sucio = True

    def cambiar_tab(d):
        nonlocal tab, sel, items, sucio
        tab = (tab + d) % len(TABS)
        sel = 0
        items = filtrar(roms, tab)
        sucio = True

    while True:
        cuadros += 1
        ahora = time.time()

        if cuadros % 30 == 0:  # revisa el USB una vez por segundo
            usb = hay_usb()
            if usb and not usb_previo:
                dibujar_menu(lienzo, items, sel, tab, len(roms),
                             "Copiando ROMS del USB...", logo_chico)
                presentar(pantalla, lienzo)
                aviso = resultado_usb(copiar_roms_usb())
                aviso_hasta = time.time() + 4
                roms = buscar_roms()
                items = filtrar(roms, tab)
                sel = 0
                sucio = True
            usb_previo = usb

        pygame.event.clear()  # el control no pasa por pygame
        for tipo, num, valor in control.eventos():
            if dialogo is not None:  # cuadro de apagado abierto: solo responde a el
                if tipo == "eje" and num == 7 and valor != 0:
                    dialogo = (dialogo + (1 if valor > 0 else -1)) % len(OPCIONES_PODER)
                    sucio = True
                elif tipo == "eje" and num == 1:  # palanca izquierda arriba/abajo
                    if abs(valor) > 20000 and dir_palanca == 0:
                        dir_palanca = 1 if valor > 0 else -1
                        dialogo = (dialogo + dir_palanca) % len(OPCIONES_PODER)
                        sucio = True
                    elif abs(valor) < 8000:
                        dir_palanca = 0
                elif tipo == "boton" and valor == 1:
                    if num == BTN_A:
                        opcion = OPCIONES_PODER[dialogo]
                        dialogo = None
                        sucio = True
                        if opcion != "CANCELAR" and not apagar_consola(pantalla, lienzo, opcion):
                            aviso = "No se pudo apagar (falta el permiso)"
                            aviso_hasta = time.time() + 4
                    elif num in (BTN_B, BTN_START):
                        dialogo = None
                        sucio = True
                continue
            if tipo == "eje" and num == 7:  # cruceta arriba/abajo
                dir_hat = (valor > 0) - (valor < 0)
                if dir_hat:
                    mover(dir_hat)
                    t_dir = t_rep = ahora
            elif tipo == "eje" and num == 1:  # palanca izquierda arriba/abajo
                if abs(valor) > 20000 and dir_palanca == 0:
                    dir_palanca = 1 if valor > 0 else -1
                    mover(dir_palanca)
                    t_dir = t_rep = ahora
                elif abs(valor) < 8000:
                    dir_palanca = 0
            elif tipo == "eje" and num == 6 and valor != 0:  # cruceta izquierda/derecha
                cambiar_tab(1 if valor > 0 else -1)
            elif tipo == "boton" and valor == 1:
                if num == BTN_LB:
                    cambiar_tab(-1)
                elif num == BTN_RB:
                    cambiar_tab(1)
                elif num == BTN_START:  # cuadro de apagado (empieza en CANCELAR)
                    dialogo = OPCIONES_PODER.index("CANCELAR")
                    sucio = True
                elif num == BTN_A and items:
                    control.cerrar()
                    return items[sel][2]

        d = dir_hat or dir_palanca  # mantener presionado = desplazamiento continuo
        if d and dialogo is None and ahora - t_dir > 0.4 and ahora - t_rep > 0.08:
            mover(d)
            t_rep = ahora

        mostrando = aviso if (aviso and ahora < aviso_hasta) else None
        if mostrando != aviso_mostrado:  # aparece o desaparece el aviso
            aviso_mostrado = mostrando
            sucio = True

        if sucio:
            dibujar_menu(lienzo, items, sel, tab, len(roms), mostrando, logo_chico, dialogo)
            presentar(pantalla, lienzo)
            sucio = False
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
    """Pantalla de inicio: logo BIT-BOY con fundido y sonido (~/assets/logo.png)."""
    pygame.font.init()
    pantalla = abrir_pantalla()
    ancho, alto = pantalla.get_size()

    logo = None
    try:
        img = pygame.image.load(os.path.join(ASSETS_DIR, "logo.png")).convert()
        k = min(ancho / img.get_width(), alto / img.get_height())
        logo = pygame.transform.smoothscale(
            img, (int(img.get_width() * k), int(img.get_height() * k)))
    except (pygame.error, FileNotFoundError):
        pass
    fondo = logo.get_at((2, 2))[:3] if logo is not None else FONDO

    sonido = None
    try:
        pygame.mixer.init(48000, -16, 2, 1024)
        sonido = generar_sonido()
    except pygame.error:
        pass  # sin audio el lanzador sigue funcionando

    def dibujar(f):
        pantalla.fill(fondo)
        if logo is not None:
            logo.set_alpha(int(255 * f))
            pantalla.blit(logo, logo.get_rect(center=(ancho // 2, alto // 2)))
        else:  # sin archivo de logo: version de texto con la misma paleta
            t = texto("BIT-BOY", 28, mezclar(fondo, TEXTO, f), max(2, alto // 140))
            pantalla.blit(t, t.get_rect(center=(ancho // 2, alto // 2 - alto // 14)))
            t = texto("8-16-32 BIT ARCADE", 22, mezclar(fondo, SELECCION, f), max(1, alto // 300))
            pantalla.blit(t, t.get_rect(center=(ancho // 2, alto // 2 + alto // 12)))
        volcar(pantalla)

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
    global proceso
    pygame.quit()  # libera la pantalla para el emulador
    time.sleep(0.5)  # deja que el sistema libere el video antes del emulador
    env = dict(os.environ, SDL_VIDEODRIVER="kmsdrm")
    env.pop("SDL_NO_SIGNAL_HANDLERS", None)  # Mednafen si debe cerrar con calma
    registrar(f"Lanzando mednafen: {ruta}")
    salida = open(os.path.expanduser("~/mednafen_ultimo.log"), "w")
    inicio = time.time()
    # Debian instala Mednafen en /usr/games, que no esta en el PATH de un servicio.
    mednafen = (shutil.which("mednafen")
                or next((p for p in ("/usr/games/mednafen", "/usr/bin/mednafen")
                         if os.path.exists(p)), "mednafen"))
    proc = subprocess.Popen(
        [mednafen, "-video.driver", "opengl", "-video.fs", "1",
         "-sound.driver", "alsa", "-sound.device", "plughw:vc4hdmi,0", ruta],
        env=env, stdout=salida, stderr=subprocess.STDOUT)
    proceso = proc

    control = Control()
    usb_previo = hay_usb()
    mensaje = None
    ticks = 0

    while proc.poll() is None:
        control.eventos()
        if {BTN_SELECT, BTN_START} <= control.pulsados:
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
    control.cerrar()
    registrar(f"mednafen termino con codigo {proc.returncode} "
              f"despues de {time.time() - inicio:.1f} s")
    if mensaje is None and proc.returncode not in (0, -15) and time.time() - inicio < 5:
        mensaje = "El juego no pudo iniciar (ver ~/mednafen_ultimo.log)"
    return mensaje


def main():
    registrar("--- inicio del lanzador ---")
    # La animacion es del encendido: si el lanzador se reinicia por cualquier
    # motivo, vuelve directo al menu. Con --intro se fuerza a mostrarla.
    if "--intro" in sys.argv or not os.path.exists(MARCA_INTRO):
        try:
            animacion_inicio()
        except Exception:
            registrar("Error en la animacion:\n" + traceback.format_exc())
        try:
            open(MARCA_INTRO, "w").close()
        except OSError:
            pass
    else:
        registrar("Reinicio del lanzador: se omite la animacion")
    mensaje = None
    try:
        if not verificar_permisos():
            mensaje = "Faltan permisos de sudo (ver ~/launcher.log)"
    except Exception:
        registrar("Error al verificar permisos:\n" + traceback.format_exc())
    while True:
        try:
            ruta = menu(mensaje)
            mensaje = jugar(ruta)
        except Exception:
            # Un error no debe reiniciar todo (y repetir el logo): se registra
            # y se vuelve al menu.
            registrar("Error:\n" + traceback.format_exc())
            matar_emulador()
            mensaje = "Ocurrio un error (ver ~/launcher.log)"
            pygame.quit()
            time.sleep(1.0)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pygame.quit()
