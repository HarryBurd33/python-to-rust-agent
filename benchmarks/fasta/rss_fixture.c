#define _GNU_SOURCE
#include <fcntl.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <unistd.h>

int main(int argc, char **argv) {
    if (argc < 2) return 2;
    if (!strcmp(argv[1], "touch")) {
        size_t size = 2 * 1024 * 1024;
        volatile unsigned char *memory = mmap(NULL, size, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
        if (memory == MAP_FAILED) return 3;
        size_t count = 0;
        for (size_t i = 0; i < size; i += 4096) { memory[i] = 1; count += memory[i]; }
        printf("{\"touch_count\":%zu,\"allocated_bytes\":%zu}\n", count, size);
        return 0;
    }
    if (!strcmp(argv[1], "exit")) return atoi(argv[2]);
    if (!strcmp(argv[1], "signal")) { raise(SIGTERM); return 3; }
    if (!strcmp(argv[1], "sleep")) { sleep(30); return 0; }
    if (!strcmp(argv[1], "residual")) { pid_t child = fork(); if (!child) { sleep(30); _exit(0); } return child < 0 ? 3 : 0; }
    if (!strcmp(argv[1], "fds")) {
        for (int fd = 3; fd < 256; ++fd) if (fcntl(fd, F_GETFD) >= 0) return 9;
        puts("fds-closed"); return 0;
    }
    if (!strcmp(argv[1], "io")) {
        char cwd[4096], input[64];
        if (!getcwd(cwd, sizeof(cwd))) return 4;
        ssize_t n = read(0, input, sizeof(input));
        printf("argv0=%s\narg=%s\ncwd=%s\nenv=%s\nstdin=%ld\n", argv[0], argv[2], cwd, getenv("RSS_TEST") ? getenv("RSS_TEST") : "", (long)n);
        fputs("fixture-stderr\n", stderr); return 0;
    }
    return 2;
}
