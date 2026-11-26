#define _GNU_SOURCE
#include <errno.h>
#include <limits.h>
#include <sched.h>
#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>
#ifndef TARGET_CPU
#error TARGET_CPU must be bound at build time
#endif
_Static_assert(TARGET_CPU >= 0 && TARGET_CPU < CPU_SETSIZE, "Target CPU outside fixed mask");
extern int rss_original_main(int argc, char **argv);
int main(int argc, char **argv) {
    if (argc < 4) return 125;
    char *end = NULL;
    long report = strtol(argv[1], &end, 10);
    if (!end || *end || report < 3 || report > INT_MAX) return 125;
    cpu_set_t requested, actual;
    CPU_ZERO(&requested); CPU_ZERO(&actual); CPU_SET(TARGET_CPU, &requested);
    if (sched_setaffinity(0, sizeof(requested), &requested) || sched_getaffinity(0, sizeof(actual), &actual)
        || CPU_COUNT(&actual) != 1 || !CPU_ISSET(TARGET_CPU, &actual)) return 125;
    char header[160];
    int size = snprintf(header, sizeof(header),
        "{\"placement_version\":1,\"supervisor_pid\":%ld,\"cpu\":%d,\"cpu_count\":1}\n",
        (long)getpid(), TARGET_CPU);
    if (size <= 0 || size >= (int)sizeof(header)) return 125;
    size_t at = 0;
    while (at < (size_t)size) {
        ssize_t count = write((int)report, header + at, (size_t)size - at);
        if (count < 0 && errno == EINTR) continue;
        if (count <= 0) return 125;
        at += (size_t)count;
    }
    return rss_original_main(argc, argv);
}
