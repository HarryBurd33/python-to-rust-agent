#define _POSIX_C_SOURCE 200809L
#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <time.h>

static unsigned long long nanos(struct timespec value) {
    return (unsigned long long)value.tv_sec * 1000000000ULL + (unsigned long long)value.tv_nsec;
}

int main(int argc, char **argv) {
    struct timespec entered, finished;
    if (clock_gettime(CLOCK_MONOTONIC, &entered)) return 3;
    if (argc != 2) return 2;
    char *end = NULL;
    long dwell = strtol(argv[1], &end, 10);
    if (!end || *end || (dwell != 0 && dwell != 3500)) return 2;
    if (dwell) {
        struct timespec deadline = entered;
        deadline.tv_nsec += dwell * 1000;
        if (deadline.tv_nsec >= 1000000000L) { ++deadline.tv_sec; deadline.tv_nsec -= 1000000000L; }
        int status;
        do { status = clock_nanosleep(CLOCK_MONOTONIC, TIMER_ABSTIME, &deadline, NULL); } while (status == EINTR);
        if (status) return 3;
    }
    if (clock_gettime(CLOCK_MONOTONIC, &finished)) return 3;
    printf("{\"dwell_us\":%ld,\"entry_monotonic_ns\":%llu,\"finish_monotonic_ns\":%llu}\n", dwell, nanos(entered), nanos(finished));
    return 0;
}
