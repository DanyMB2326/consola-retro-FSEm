#!/bin/bash
# BIT-BOY: prueba que controlador de video de Mednafen se ve en pantalla.
#
# Autores: Daniela Martinez | Judith Mora | Luis Hernández
# Licencia: MIT
# Prueba que driver de video de Mednafen se ve en kmsdrm. Uso: ./prueba_mednafen.sh /ruta/juego.nes

sudo systemctl stop retro.service
export SDL_VIDEODRIVER=kmsdrm
for d in opengl sdl softfb; do
  echo ">>> Driver: $d (8 s). Anota si ves imagen."
  timeout 8 /usr/games/mednafen -video.driver $d -video.fs 1 -sound.driver alsa -sound.device plughw:vc4hdmi,0 "$1" >/tmp/mn_$d.log 2>&1
  tail -3 /tmp/mn_$d.log
done
