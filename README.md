# BIT-BOY: consola retro con Raspberry Pi 3 B+

Consola de videojuegos retro que emula **NES, SNES y Game Boy Advance** con
[Mednafen](https://mednafen.github.io/documentation). Arranca sola al conectar la
corriente, muestra un logo con sonido, lista los juegos y se controla **solo con un
gamepad** USB tipo Xbox. No usa escritorio, teclado ni mouse.

Proyecto final de *Fundamentos de Sistemas Embebidos* (UNAM, Facultad de Ingeniería),
opción 5.4 "Consola de videojuegos Retro".

- **Autora:** Daniela Martínez | Judith Mora | Luis Hernández
- **Licencia:** MIT (ver `LICENSE`)
- **Video demostrativo:** ---
- **Repositorio:** ---

## Qué hace

- Logo BIT-BOY con tono de arranque, sin texto de consola visible.
- Arranque directo al lanzador (servicio de systemd), sin escritorio.
- Menú con pestañas Todos / NES / SNES / GBA, controlado con cruceta, palanca y botones.
- Al conectar una memoria USB, copia las ROMS nuevas (sin duplicados, comparando MD5), incluso durante un juego.
- Select + Start cierran el juego y vuelven al menú. Start en el menú abre Apagar / Reiniciar / Cancelar.

## Materiales

Raspberry Pi 3 B+, microSD de 8 GB o más, fuente de 5 V y 2.5 A, gamepad GameSir K1
(o cualquier gamepad USB tipo Xbox), cable HDMI y pantalla con audio, memoria USB para ROMS.

## Instalación automática

1. Instala **Raspberry Pi OS Lite** en la microSD con Raspberry Pi Imager (crea tu usuario y activa SSH).
2. Enciende la Raspberry con internet y entra por SSH.
3. Descarga e instala:

   ```
   sudo apt-get install -y git
   git clone https://github.com/USUARIO/REPOSITORIO.git
   cd REPOSITORIO/src
   ./install.sh
   ```

   El script instala los paquetes, configura video, audio, arranque silencioso, permisos
   y el servicio, y reinicia. Se puede repetir sin problema. Con `./install.sh --no-reboot`
   no reinicia al terminar.
4. Conecta el gamepad y reinicia si hace falta. La consola arranca sola.

### Mapeo del gamepad (una sola vez)

Mednafen crea su archivo de configuración al abrir un juego por primera vez. Después de
instalar y reiniciar:

1. Abre un juego cualquiera desde el menú y sal con Select + Start.
2. Ejecuta `cd REPOSITORIO/src && ./mapear_control.sh`. Asigna A, B, X, Y, L, R, Select,
   Start y la cruceta del GameSir K1 en NES, SNES y GBA.
3. Comprueba: `grep -E 'gamepad\.(a|b|x|y) ' ~/.mednafen/mednafen.cfg | grep -E 'nes|gba'`.

Si el paquete trae `src/mednafen.cfg`, el instalador lo usa y este paso no hace falta.
Con otro modelo de gamepad, cambia el identificador `ID` al inicio de `mapear_control.sh`
(se obtiene con `grep -i "unique id" ~/.mednafen/*.log` o el registro de Mednafen).

## Contenido

| Archivo | Función |
| --- | --- |
| `src/install.sh` | Instalador automático para una instalación limpia |
| `src/launcher.py` | Lanzador: menú, intro, USB, apagado |
| `src/mapear_control.sh` | Asigna los botones del gamepad a Mednafen |
| `src/verificar.sh` | Revisión rápida de salud (alimentación, servicio, permisos, ROMs) |
| `src/probar_roms.sh` | Abre cada ROM unos segundos y lista las que no arrancan |
| `src/prueba_mednafen.sh` | Prueba qué controlador de video de Mednafen se ve en la pantalla |
| `src/retro.service` | Servicio de systemd (plantilla) |
| `src/asound.conf` | Audio por HDMI |
| `src/assets/` | Logo e imagen del encabezado |
| `src/roms/` | ROMS que se precargan |
| `doc/` | Reporte final (PDF) |
| `vid/` | Enlace al video |

## Uso

- **Cruceta o palanca:** moverse. **LB / RB o cruceta izquierda-derecha:** cambiar de pestaña.
- **A:** abrir juego. **Select + Start:** salir del juego. **Start (en el menú):** apagar o reiniciar.
- **Memoria USB:** conéctala y espera el aviso; las ROMS nuevas aparecen en la lista.

## Solución de problemas

- **Pantalla negra con sonido:** en la Pi 3, Mesa 26.x (repositorio de Raspberry) deja SDL con `kmsdrm` en negro. El instalador baja Mesa a la 25.0.7 de Debian y la fija con `apt-mark hold`. Para revisarlo: `dpkg -l libegl-mesa0 | tail -1`.

- El registro del lanzador está en `~/launcher.log` y el de Mednafen en `~/mednafen_ultimo.log`.
- Si el gamepad se desconecta solo, revisa la fuente: `vcgencmd get_throttled` debe dar `0x0`.
- Si una memoria USB no se lee, el motivo exacto queda en `~/launcher.log`.

## Aviso sobre las ROMS

Este repositorio solo debe incluir ROMS que se puedan redistribuir. Mednafen y pygame
se instalan desde los repositorios de Raspberry Pi OS y mantienen sus propias licencias.
