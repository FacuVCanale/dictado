/* Ejecutable de Dictado.app. Lanza el python del venv como HIJO y se queda vivo como padre:
 * así el proceso responsable ante TCC es la app (su bundle id) y no el binario de python.
 * Un exec() le pasaría la identidad a python. Propaga SIGTERM/SIGINT/SIGHUP y el exit code; si el
 * hijo muere por una señal, el launcher muere por la misma señal, para que launchd distinga un
 * crash (KeepAlive.Crashed) de una salida normal. */
#include <errno.h>
#include <signal.h>
#include <spawn.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/wait.h>
#include <unistd.h>

#ifndef DICTADO_PYTHON
#error "compilar con -DDICTADO_PYTHON=\"/ruta/al/.venv/bin/python\""
#endif

extern char **environ;
static volatile pid_t child = 0;

static void forward(int sig) {
    if (child > 0) kill(child, sig);
}

int main(int argc, char **argv) {
    /* argv del hijo: python -m dictado.app <args del launcher> */
    char **cargv = calloc((size_t)argc + 3, sizeof(char *));
    if (!cargv) return 70;
    cargv[0] = DICTADO_PYTHON;
    cargv[1] = "-m";
    cargv[2] = "dictado.app";
    for (int i = 1; i < argc; i++) cargv[i + 2] = argv[i];

    struct sigaction sa;
    memset(&sa, 0, sizeof sa);
    sa.sa_handler = forward;
    sigemptyset(&sa.sa_mask);
    sigaction(SIGTERM, &sa, NULL);
    sigaction(SIGINT, &sa, NULL);
    sigaction(SIGHUP, &sa, NULL);

    pid_t pid;
    int rc = posix_spawn(&pid, DICTADO_PYTHON, NULL, NULL, cargv, environ);
    if (rc != 0) {
        fprintf(stderr, "Dictado: no pude lanzar %s: %s\n", DICTADO_PYTHON, strerror(rc));
        return 71;
    }
    child = pid;

    int status;
    while (waitpid(pid, &status, 0) < 0) {
        if (errno != EINTR) { perror("Dictado: waitpid"); return 72; }
    }
    if (WIFEXITED(status)) return WEXITSTATUS(status);
    if (WIFSIGNALED(status)) {
        int sig = WTERMSIG(status);
        signal(sig, SIG_DFL);
        kill(getpid(), sig);
        return 128 + sig;  /* sólo si la señal no terminó el proceso */
    }
    return 1;
}
