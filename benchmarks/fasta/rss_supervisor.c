#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <sys/resource.h>
#include <sys/types.h>
#include <sys/wait.h>
#include <unistd.h>

extern char **environ;
struct frame { uint64_t ceiling; int32_t error; uint32_t kind; };

static int write_all(int fd, const void *data, size_t size) {
    const char *p = data;
    while (size) {
        ssize_t n = write(fd, p, size);
        if (n < 0 && errno == EINTR) continue;
        if (n <= 0) return -1;
        p += n; size -= (size_t)n;
    }
    return 0;
}

__attribute__((noinline)) static void prefault_stack(void) {
    volatile unsigned char deeper[32768];
    for (size_t i = 0; i < sizeof(deeper); ++i) deeper[i] = 0;
}

static void launch(int pipefd, int reportfd, long pagesize, char *path, char **argv) {
    /* Pre-touch more stack than the statm/exec syscall path uses. No allocator,
       stdio, pathname construction or dynamic linker is used after the read. */
    char buffer[256];
    struct frame frame = {0, 0, 1};
    prefault_stack();
    close(reportfd);
    int fd = open("/proc/self/statm", O_RDONLY | O_CLOEXEC);
    ssize_t length = fd < 0 ? -1 : read(fd, buffer, sizeof(buffer));
    int read_error = errno;
    if (fd >= 0) close(fd);
    /* From here to execve: fixed buffers and bounded syscalls only. */
    uint64_t pages = 0;
    size_t at = 0;
    if (length <= 0) frame.error = read_error ? read_error : EIO;
    while (!frame.error && at < (size_t)length && buffer[at] >= '0' && buffer[at] <= '9') {
        unsigned digit = (unsigned)(buffer[at++] - '0');
        if (pages > (UINT64_MAX - digit) / 10) frame.error = EOVERFLOW;
        else pages = pages * 10 + digit;
    }
    if (!frame.error && (!at || at == (size_t)length || buffer[at] != ' ' || !pages ||
                         pages > UINT64_MAX / (uint64_t)pagesize)) frame.error = EINVAL;
    if (!frame.error) frame.ceiling = pages * (uint64_t)pagesize;
    if (write_all(pipefd, &frame, sizeof(frame)) || frame.error) _exit(126);
    execve(path, argv, environ);
    frame.kind = 2; frame.error = errno;
    (void)write_all(pipefd, &frame, sizeof(frame));
    _exit(126);
}

int main(int argc, char **argv) {
    if (argc < 4) return 125;
    char *end = NULL;
    long report = strtol(argv[1], &end, 10);
    long pagesize = sysconf(_SC_PAGESIZE);
    if (!end || *end || report < 3 || report > INT_MAX || pagesize <= 0) return 125;
    int channel[2];
    if (pipe2(channel, O_CLOEXEC)) return 125;
    pid_t target = fork();
    if (target < 0) return 125;
    if (!target) {
        close(channel[0]);
        launch(channel[1], (int)report, pagesize, argv[2], argv + 3);
    }
    close(channel[1]);
    struct rusage usage;
    int status;
    pid_t result;
    do { result = wait4(target, &status, 0, &usage); } while (result < 0 && errno == EINTR);
    if (result != target) return 125;
    struct frame messages[3];
    size_t used = 0;
    for (;;) {
        ssize_t n = read(channel[0], (char *)messages + used, sizeof(messages) - used);
        if (n < 0 && errno == EINTR) continue;
        if (n < 0 || (n > 0 && used + (size_t)n == sizeof(messages))) return 125;
        if (!n) break;
        used += (size_t)n;
    }
    close(channel[0]);
    if (used != sizeof(struct frame) && used != 2 * sizeof(struct frame)) return 125;
    if (messages[0].kind != 1) return 125;
    int exec_error = messages[0].error;
    if (used == 2 * sizeof(struct frame)) {
        if (messages[1].kind != 2 || messages[1].ceiling != messages[0].ceiling || messages[1].error <= 0 || exec_error) return 125;
        exec_error = messages[1].error;
    }
    char output[512];
    int n = snprintf(output, sizeof(output),
        "{\"version\":1,\"supervisor_pid\":%ld,\"target_pid\":%ld,\"wait_status\":%d,\"target_rss_kib\":%ld,\"preexec_ceiling_bytes\":%llu,\"exec_errno\":%d}\n",
        (long)getpid(), (long)target, status, usage.ru_maxrss,
        (unsigned long long)messages[0].ceiling, exec_error);
    if (n <= 0 || n >= (int)sizeof(output) || write_all((int)report, output, (size_t)n)) return 125;
    close((int)report);
    return 0;
}
