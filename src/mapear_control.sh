#!/usr/bin/env bash
# BIT-BOY: asigna los botones del gamepad GameSir K1 (tipo Xbox) a Mednafen.
#
# Mednafen solo crea ~/.mednafen/mednafen.cfg despues de abrir un juego una vez.
#
# Uso:  ./mapear_control.sh            (usa ~/.mednafen/mednafen.cfg)
#       CFG=/ruta/mednafen.cfg ./mapear_control.sh
#
# Autor: Daniela Martinez | Judith Mora | Luis Hernández  Licencia: MIT
set -euo pipefail

CFG="${CFG:-$HOME/.mednafen/mednafen.cfg}"

ID="${ID:-0x00033537105802010008000f00000000}"

[ -f "$CFG" ] || { echo "Aun no existe $CFG. Abre un juego una vez y repite."; exit 2; }
[ -f "$CFG.bak-mapeo" ] || cp "$CFG" "$CFG.bak-mapeo"

cambiados=0
set_k() {
    local n
    n=$(grep -c -E "^$1 " "$CFG" || true)
    if [ "$n" -gt 0 ]; then
        sed -i -E "s#^($1) .*#\1 joystick $ID $2#" "$CFG"
        cambiados=$((cambiados + n))
    fi
}

SIS='(nes|snes|snes_faust|gba)\.input\.(port1|builtin)\.gamepad'
set_k "$SIS\.a"      button_0
set_k "$SIS\.b"      button_1
set_k "$SIS\.x"      button_3
set_k "$SIS\.y"      button_4
set_k "$SIS\.(l|lshoulder|shoulder_l)" button_6
set_k "$SIS\.(r|rshoulder|shoulder_r)" button_7
set_k "$SIS\.select" button_10
set_k "$SIS\.start"  button_11
set_k "$SIS\.up"     'abs_7-'
set_k "$SIS\.down"   'abs_7+'
set_k "$SIS\.left"   'abs_6-'
set_k "$SIS\.right"  'abs_6+'

if [ "$cambiados" -eq 0 ]; then
    echo "No encontre botones en $CFG todavia. Abre un juego una vez y repite."
    exit 2
fi
echo "Listo: $cambiados asignaciones actualizadas. Respaldo: $CFG.bak-mapeo"
echo "Comprueba con:"
echo "  grep -E 'gamepad\\.(a|b|x|y) ' $CFG | grep -E 'nes|gba'"
