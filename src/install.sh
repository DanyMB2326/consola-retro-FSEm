#!/usr/bin/env bash
# BIT-BOY: instalador de la consola retro (NES, SNES y GBA) para Raspberry Pi 3 B+.
#
# Convierte una instalacion limpia de Raspberry Pi OS Lite (sin escritorio) en una
# consola que arranca sola: logo con sonido, menu de juegos y emulacion con Mednafen,
# controlada solo con un gamepad. Se puede ejecutar varias veces sin danar nada.
#
# Uso (como tu usuario normal, NO como root):
#     cd src && ./install.sh              # instala y reinicia al terminar
#     ./install.sh --no-reboot            # instala sin reiniciar
#
# Autor: Daniela Martinez | Judith Mora | Luis Hernández
# Licencia: MIT

set -euo pipefail

REINICIAR=1
[ "${1:-}" = "--no-reboot" ] && REINICIAR=0

# Rutas (se pueden sustituir con variables de entorno para pruebas)
BOOT_DIR="${BOOT_DIR:-/boot/firmware}"
SUDOERS_DIR="${SUDOERS_DIR:-/etc/sudoers.d}"
SYSTEMD_DIR="${SYSTEMD_DIR:-/etc/systemd/system}"
ASOUND_CONF="${ASOUND_CONF:-/etc/asound.conf}"
SOLO_ARCHIVOS="${SOLO_ARCHIVOS:-0}"   # 1 = no instala paquetes ni toca systemd (pruebas)

AQUI="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
USUARIO="${USUARIO:-$(id -un)}"
CASA="${CASA:-$(getent passwd "$USUARIO" | cut -d: -f6)}"

paso() { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }
aviso() { printf '\033[1;33m[aviso]\033[0m %s\n' "$*"; }
falla() { printf '\033[1;31m[error]\033[0m %s\n' "$*" >&2; exit 1; }
sudo_() { if [ "$SOLO_ARCHIVOS" = 1 ]; then "$@"; else sudo "$@"; fi; }

paso "Comprobando el sistema"
[ "$(id -u)" -ne 0 ] || [ "$SOLO_ARCHIVOS" = 1 ] || falla "Ejecuta el script como tu usuario normal, no con sudo."
[ -f "$AQUI/launcher.py" ] || falla "No encuentro launcher.py junto al script."
if [ "$SOLO_ARCHIVOS" != 1 ]; then
    [ -d "$BOOT_DIR" ] || falla "No existe $BOOT_DIR: esto no parece Raspberry Pi OS."
    if dpkg -l 2>/dev/null | grep -q -E '^ii +(lightdm|gdm3|sddm|raspberrypi-ui-mods|labwc|wayfire)'; then
        aviso "Se detecto un escritorio instalado. El proyecto exige Raspberry Pi OS Lite (sin escritorio)."
    fi
    sudo -v || falla "Se necesita sudo para instalar."
fi
echo "Usuario: $USUARIO   Carpeta personal: $CASA"

if [ "$SOLO_ARCHIVOS" != 1 ]; then
    paso "Instalando Mednafen y pygame"
    sudo apt-get update
    # libegl/libgbm/mesa: SDL las necesita para dibujar directo en la pantalla (kmsdrm)
    sudo apt-get install -y mednafen python3-pygame alsa-utils joystick exfatprogs ntfs-3g \
        libegl1 libgles2 libgbm1 libegl-mesa0 libgl1-mesa-dri

    # Mesa 26.x (repositorio de Raspberry) deja la pantalla en negro con SDL + kmsdrm
    # en la Pi 3. Se baja a la version estable de Debian y se fija para que no se
    # actualice sola.
    MESA_VER="25.0.7-2+deb13u1"
    if apt-cache madison mesa-libgallium | grep -q "$MESA_VER"; then
        paso "Fijando Mesa $MESA_VER (estable)"
        PAQ="libegl-mesa0 libgl1-mesa-dri mesa-libgallium libgbm1"
        dpkg -s mesa-vulkan-drivers >/dev/null 2>&1 && PAQ="$PAQ mesa-vulkan-drivers"
        ARGS=""
        for p in $PAQ; do ARGS="$ARGS $p=$MESA_VER"; done
        sudo apt-get install -y --allow-downgrades $ARGS
        sudo apt-mark hold $PAQ
    else
        aviso "No hay Mesa $MESA_VER disponible; si la pantalla queda en negro, ver readme.MD."
    fi
fi

paso "Permisos de usuario para video, gamepad y audio"
for g in video render input audio; do
    if getent group "$g" >/dev/null; then
        sudo_ usermod -aG "$g" "$USUARIO" 2>/dev/null || true
    fi
done

paso "Copiando el lanzador, los recursos y las ROMS"
install -m 755 "$AQUI/launcher.py" "$CASA/launcher.py"
mkdir -p "$CASA/assets" "$CASA/roms/nes" "$CASA/roms/snes" "$CASA/roms/gba"
cp -f "$AQUI"/assets/*.png "$CASA/assets/"
total=0
for sis in nes snes gba; do
    for f in "$AQUI/roms/$sis"/*; do
        [ -f "$f" ] || continue
        case "$(basename "$f")" in .gitkeep|LEEME*) continue ;; esac
        cp -n "$f" "$CASA/roms/$sis/" && total=$((total + 1))
    done
done
echo "ROMS copiadas desde el paquete: $total"

if [ -f "$AQUI/mednafen.cfg" ]; then
    mkdir -p "$CASA/.mednafen"
    [ -f "$CASA/.mednafen/mednafen.cfg" ] && [ ! -f "$CASA/.mednafen/mednafen.cfg.bak-bitboy" ] \
        && cp "$CASA/.mednafen/mednafen.cfg" "$CASA/.mednafen/mednafen.cfg.bak-bitboy"
    cp -f "$AQUI/mednafen.cfg" "$CASA/.mednafen/mednafen.cfg"
    echo "Mapeo del gamepad instalado (mednafen.cfg)"
else
    # sin mednafen.cfg propio: intenta asignar el GameSir K1 al archivo que ya exista
    if [ -f "$CASA/.mednafen/mednafen.cfg" ]; then
        CFG="$CASA/.mednafen/mednafen.cfg" "$AQUI/mapear_control.sh" || true
    fi
    aviso "Mapeo del gamepad: despues del primer arranque abre un juego una vez, sal con Select + Start y ejecuta ./mapear_control.sh"
fi
[ "$SOLO_ARCHIVOS" = 1 ] || sudo chown -R "$USUARIO":"$USUARIO" "$CASA/launcher.py" "$CASA/assets" "$CASA/roms" "$CASA/.mednafen" 2>/dev/null || true

paso "Audio por HDMI"
sudo_ install -m 644 "$AQUI/asound.conf" "$ASOUND_CONF"

paso "Video KMS y arranque sin pantalla de inicio"
CONFIG="$BOOT_DIR/config.txt"
[ -f "$CONFIG" ] || falla "No existe $CONFIG"
[ -f "$CONFIG.bak-bitboy" ] || sudo_ cp "$CONFIG" "$CONFIG.bak-bitboy"
# desactiva el controlador de video antiguo (incompatible con kmsdrm)
sudo_ sed -i 's/^dtoverlay=vc4-fkms-v3d/#dtoverlay=vc4-fkms-v3d/' "$CONFIG"
if ! grep -q '^dtoverlay=vc4-kms-v3d' "$CONFIG"; then
    printf '\n# BIT-BOY\ndtoverlay=vc4-kms-v3d\n' | sudo_ tee -a "$CONFIG" >/dev/null
fi
if grep -q '^disable_splash=' "$CONFIG"; then
    sudo_ sed -i 's/^disable_splash=.*/disable_splash=1/' "$CONFIG"
else
    printf 'disable_splash=1\n' | sudo_ tee -a "$CONFIG" >/dev/null
fi

paso "Arranque sin texto de consola"
CMDLINE="$BOOT_DIR/cmdline.txt"
[ -f "$CMDLINE" ] || falla "No existe $CMDLINE"
[ -f "$CMDLINE.bak-bitboy" ] || sudo_ cp "$CMDLINE" "$CMDLINE.bak-bitboy"
linea="$(tr '\n' ' ' < "$CMDLINE" | sed 's/  */ /g; s/ *$//')"
# la consola de texto pasa a tty3 (que nadie mira)
linea="$(printf '%s' "$linea" | sed -E 's/(^| )console=tty[0-9]+/\1console=tty3/g')"
printf '%s' "$linea" | grep -q 'console=tty3' || linea="$linea console=tty3"
for par in quiet loglevel=0 logo.nologo vt.global_cursor_default=0 systemd.show_status=0 consoleblank=0 vt.color=0x00; do
    clave="${par%%=*}"
    if printf '%s' "$linea" | grep -qE "(^| )${clave//./\\.}(=|$| )"; then
        # ya existe: se fuerza el valor correcto
        [ "$clave" = "$par" ] || linea="$(printf '%s' "$linea" | sed -E "s/(^| )${clave//./\\.}=[^ ]*/\1$par/")"
    else
        linea="$linea $par"
    fi
done
printf '%s\n' "$linea" | sudo_ tee "$CMDLINE" >/dev/null
[ "$(wc -l < "$CMDLINE")" -eq 1 ] || falla "cmdline.txt debe tener una sola linea"

paso "Permisos de sudo (USB y apagado)"
sudo_ mkdir -p /mnt/usb 2>/dev/null || true
tmp="$(mktemp)"
cat > "$tmp" <<REGLAS
$USUARIO ALL=(root) NOPASSWD: /usr/bin/mount -o ro /dev/sd* /mnt/usb, /usr/bin/umount /mnt/usb, /usr/bin/systemctl poweroff, /usr/bin/systemctl reboot
REGLAS
if visudo -cf "$tmp" >/dev/null 2>&1; then
    sudo_ install -m 440 -o root -g root "$tmp" "$SUDOERS_DIR/retro"
    echo "Permisos instalados en $SUDOERS_DIR/retro"
else
    rm -f "$tmp"; falla "La regla de sudoers no es valida; no se instalo."
fi
rm -f "$tmp"

paso "Servicio de arranque automatico"
tmp="$(mktemp)"
sed -e "s|__USER__|$USUARIO|g" -e "s|__HOME__|$CASA|g" "$AQUI/retro.service" > "$tmp"
sudo_ install -m 644 "$tmp" "$SYSTEMD_DIR/retro.service"
rm -f "$tmp"
if [ "$SOLO_ARCHIVOS" != 1 ]; then
    sudo systemctl mask getty@tty1.service        # nada mas usa la pantalla
    sudo systemctl daemon-reload
    sudo systemctl enable retro.service
    # arranque mas rapido: cloud-init no se usa en una consola
    [ -d /etc/cloud ] && sudo touch /etc/cloud/cloud-init.disabled || true
fi

paso "Verificando"
ok=1
chequeo() { if eval "$2"; then echo "  [ok] $1"; else echo "  [FALLA] $1"; ok=0; fi; }
chequeo "launcher.py instalado"           "[ -f '$CASA/launcher.py' ]"
chequeo "logo instalado"                  "[ -f '$CASA/assets/logo.png' ]"
chequeo "audio HDMI configurado"          "grep -q vc4hdmi '$ASOUND_CONF'"
chequeo "KMS activado en config.txt"      "grep -q '^dtoverlay=vc4-kms-v3d' '$CONFIG'"
chequeo "arranque silencioso"             "grep -q 'vt.color=0x00' '$CMDLINE'"
chequeo "permisos de sudo"                "[ -f '$SUDOERS_DIR/retro' ]"
chequeo "servicio instalado"              "[ -f '$SYSTEMD_DIR/retro.service' ]"
if [ "$SOLO_ARCHIVOS" != 1 ]; then
    chequeo "mednafen disponible"         "[ -x /usr/games/mednafen ]"
    chequeo "sudo sin contrasena (apagado)" "sudo -k; sudo -n -l /usr/bin/systemctl poweroff >/dev/null 2>&1"
    chequeo "servicio habilitado"         "systemctl is-enabled retro.service >/dev/null 2>&1"
fi
[ "$ok" = 1 ] || falla "Alguna comprobacion fallo; revisa los mensajes anteriores."

paso "Listo"
echo "Instalacion terminada. Conecta el gamepad antes de encender."
if [ "$SOLO_ARCHIVOS" != 1 ]; then
    if [ "$REINICIAR" = 1 ]; then
        echo "Reiniciando en 5 segundos (Ctrl+C para cancelar)..."; sleep 5; sudo reboot
    else
        echo "Reinicia con: sudo reboot"
    fi
fi
