#!/bin/bash
# BIT-BOY: revision rapida de salud de la consola antes de una demo.
#
# Autores: Daniela Martinez | Judith Mora | Luis Hernández
# Licencia: MIT
# BIT-BOY: revision rapida de de la consola antes de una entrega.

ok(){ echo "[ok]    $1"; }; mal(){ echo "[FALLA] $1"; fallos=$((fallos+1)); }
fallos=0
t=$(vcgencmd get_throttled | cut -d= -f2)
[ "$t" = "0x0" ] && ok "Alimentacion estable ($t)" || mal "Alimentacion/temperatura: $t (0x50000 = hubo subvoltaje)"
echo "        temperatura: $(vcgencmd measure_temp)"
systemctl is-enabled retro.service >/dev/null 2>&1 && ok "retro.service habilitado" || mal "retro.service NO habilitado"
systemctl is-active  retro.service >/dev/null 2>&1 && ok "launcher corriendo" || mal "launcher detenido"
grep -q "quiet" /boot/firmware/cmdline.txt && grep -q "console=tty3" /boot/firmware/cmdline.txt && ok "arranque sin texto (cmdline)" || mal "cmdline sin quiet/console=tty3"
grep -q "^dtoverlay=vc4-kms-v3d" /boot/firmware/config.txt && ok "config.txt: vc4-kms-v3d" || mal "config.txt sin vc4-kms-v3d"
[ -f /etc/sudoers.d/retro ] && sudo -k && sudo -n -l /usr/bin/systemctl poweroff >/dev/null 2>&1 && ok "permiso de apagado" || mal "permiso de apagado (sudoers)"
[ -f ~/.mednafen/mednafen.cfg ] && grep -qE "gamepad\.a .*button_0" ~/.mednafen/mednafen.cfg && ok "botones mapeados en Mednafen" || mal "botones sin mapear (bash ~/mapear_control.sh)"
[ -e /dev/input/js0 ] && ok "control detectado" || mal "control NO detectado (/dev/input/js0)"
for s in nes snes gba; do n=$(ls ~/roms/$s 2>/dev/null | wc -l); [ "$n" -gt 0 ] && ok "ROMs $s: $n" || mal "ROMs $s: 0"; done
[ -f ~/assets/logo.png ] && ok "logo presente" || mal "falta ~/assets/logo.png"
echo; [ "$fallos" -eq 0 ] && echo "TODO LISTO" || echo "$fallos punto(s) por corregir"
