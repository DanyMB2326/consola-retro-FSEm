#!/bin/bash
# BIT-BOY: abre cada ROM unos segundos y avisa cuales no arrancan.
# Uso: bash probar_roms.sh [segundos por juego, por defecto 6]
SEG="${1:-6}"
sudo systemctl stop retro.service
export SDL_VIDEODRIVER=kmsdrm
mal=0; total=0
for f in "$HOME"/roms/nes/* "$HOME"/roms/snes/* "$HOME"/roms/gba/*; do
  [ -f "$f" ] || continue
  total=$((total+1))
  printf '%2d  %-50s ' "$total" "$(basename "$f")"
  timeout "$SEG" /usr/games/mednafen -video.driver opengl -video.fs 1 \
      -sound.driver alsa -sound.device plughw:vc4hdmi,0 "$f" >/tmp/rom_test.log 2>&1
  rc=$?
  if [ "$rc" -eq 124 ]; then echo "OK (siguio corriendo)"
  else echo "FALLO (codigo $rc): $(grep -a -i -m1 'error\|fail\|unsupported' /tmp/rom_test.log)"; mal=$((mal+1)); fi
done
echo; echo "Probadas: $total   Con fallo: $mal"
sudo systemctl start retro.service
