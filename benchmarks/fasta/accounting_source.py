"""Exact-once, hash-guarded parent-only additions to the original supervisor."""
import hashlib

ORIGINAL_SHA256='a1d6f8be5ec45b3fe9fec620262664c0dd0058d7d56cf95b4ea82b07df51a1ef'


def generate(source):
    if hashlib.sha256(source.encode()).hexdigest()!=ORIGINAL_SHA256:
        raise ValueError('Original RSS supervisor source identity changed')
    changes=[('#include <unistd.h>','#include <unistd.h>\n#include <time.h>'),
        ('    pid_t target = fork();','    struct timespec fork_before, wait_after;\n    if (clock_gettime(CLOCK_MONOTONIC, &fork_before)) return 125;\n    pid_t target = fork();'),
        ('    do { result = wait4(target, &status, 0, &usage); } while (result < 0 && errno == EINTR);',
         '    do {\n        result = wait4(target, &status, 0, &usage);\n        if (result == target && clock_gettime(CLOCK_MONOTONIC, &wait_after)) return 125;\n    } while (result < 0 && errno == EINTR);'),
        ('    close((int)report);\n    return 0;',r'''    char accounting[1024];
    int accounting_size = snprintf(accounting, sizeof(accounting),
        "{\"accounting_version\":1,\"supervisor_pid\":%ld,\"target_pid\":%ld,\"wait_status\":%d,\"target_rss_kib\":%ld,\"fork_monotonic_ns\":%llu,\"reap_monotonic_ns\":%llu,\"user_sec\":%ld,\"user_usec\":%ld,\"system_sec\":%ld,\"system_usec\":%ld,\"minor_faults\":%ld,\"major_faults\":%ld,\"block_inputs\":%ld,\"block_outputs\":%ld,\"voluntary_switches\":%ld,\"involuntary_switches\":%ld}\n",
        (long)getpid(), (long)target, status, usage.ru_maxrss,
        (unsigned long long)fork_before.tv_sec * 1000000000ULL + (unsigned long long)fork_before.tv_nsec,
        (unsigned long long)wait_after.tv_sec * 1000000000ULL + (unsigned long long)wait_after.tv_nsec,
        (long)usage.ru_utime.tv_sec, (long)usage.ru_utime.tv_usec, (long)usage.ru_stime.tv_sec, (long)usage.ru_stime.tv_usec,
        usage.ru_minflt, usage.ru_majflt, usage.ru_inblock, usage.ru_oublock, usage.ru_nvcsw, usage.ru_nivcsw);
    if (accounting_size <= 0 || accounting_size >= (int)sizeof(accounting)
        || write_all((int)report, accounting, (size_t)accounting_size)) return 125;
    close((int)report);
    return 0;''')]
    result=source
    for old,new in changes:
        if result.count(old)!=1:raise ValueError('Native source replacement is not exact-once')
        result=result.replace(old,new,1)
    child_start=source.index('static void launch(');child_stop=source.index('\nint main(',child_start)
    if source[child_start:child_stop]!=result[result.index('static void launch('):result.index('\nint main(')]:
        raise ValueError('Original child pre-exec function changed')
    branch='''    if (!target) {
        close(channel[0]);
        launch(channel[1], (int)report, pagesize, argv[2], argv + 3);
    }'''
    if source.count(branch)!=1 or result.count(branch)!=1:raise ValueError('Original child branch changed')
    # The original RSS snprintf/write block must remain literally contiguous.
    original_report=source[source.index('    char output[512];'):source.index('    close((int)report);')]
    if result.count(original_report)!=1:raise ValueError('Original RSS JSON report changed')
    return result
