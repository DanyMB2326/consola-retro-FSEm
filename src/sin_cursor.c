/*
 * BIT-BOY: oculta la flecha del raton cuando arranca Mednafen.
 * Autor: Daniela Martinez | Judith Mora | Luis Hernández
 * Licencia: MIT
 */
#define _GNU_SOURCE
#include <dlfcn.h>
#include <stdint.h>

int drmModeSetCursor(int fd, uint32_t crtc_id, uint32_t bo_handle,
                     uint32_t width, uint32_t height)
{
    static int (*real)(int, uint32_t, uint32_t, uint32_t, uint32_t);
    (void)bo_handle;
    if (!real)
        real = dlsym(RTLD_NEXT, "drmModeSetCursor");
    return real ? real(fd, crtc_id, 0, width, height) : 0;
}

int drmModeSetCursor2(int fd, uint32_t crtc_id, uint32_t bo_handle,
                      uint32_t width, uint32_t height, int32_t hot_x, int32_t hot_y)
{
    static int (*real)(int, uint32_t, uint32_t, uint32_t, uint32_t, int32_t, int32_t);
    (void)bo_handle;
    if (!real)
        real = dlsym(RTLD_NEXT, "drmModeSetCursor2");
    return real ? real(fd, crtc_id, 0, width, height, hot_x, hot_y) : 0;
}

int drmModeMoveCursor(int fd, uint32_t crtc_id, int x, int y)
{
    (void)fd; (void)crtc_id; (void)x; (void)y;
    return 0;
}
