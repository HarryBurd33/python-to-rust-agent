#define _GNU_SOURCE
#include <errno.h>
#include <sched.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/resource.h>
#include <time.h>
#include <unistd.h>
static unsigned long long ns(struct timespec t) { return (unsigned long long)t.tv_sec*1000000000ULL+(unsigned long long)t.tv_nsec; }
int main(int argc,char **argv) {
    struct timespec entered,cpu_entered,cpu_finished,finished,now;
    if (clock_gettime(CLOCK_MONOTONIC,&entered) || clock_gettime(CLOCK_THREAD_CPUTIME_ID,&cpu_entered)) return 3;
    if (argc!=3 || (strcmp(argv[1],"busy") && strcmp(argv[1],"sleeping"))) return 2;
    char *end=NULL;long budget=strtol(argv[2],&end,10);
    if (!end || *end || (budget!=500 && budget!=250000)) return 2;
    cpu_set_t mask;CPU_ZERO(&mask);
    if (sched_getaffinity(0,sizeof(mask),&mask)) return 3;
    int count=CPU_COUNT(&mask),cpu=-1;
    for (int i=0;i<CPU_SETSIZE;++i) if (CPU_ISSET(i,&mask)) { cpu=i;break; }
    struct timespec deadline=entered;deadline.tv_nsec+=budget*1000L;
    if (deadline.tv_nsec>=1000000000L) { ++deadline.tv_sec;deadline.tv_nsec-=1000000000L; }
    if (!strcmp(argv[1],"busy")) {
        do { if (clock_gettime(CLOCK_MONOTONIC,&now)) return 3; } while (ns(now)<ns(deadline));
    } else {
        int status;
        do { status=clock_nanosleep(CLOCK_MONOTONIC,TIMER_ABSTIME,&deadline,NULL); } while (status==EINTR);
        if (status) return 3;
    }
    int current=sched_getcpu();
    if (current<0 || clock_gettime(CLOCK_THREAD_CPUTIME_ID,&cpu_finished) || clock_gettime(CLOCK_MONOTONIC,&finished)) return 3;
    struct rusage usage;
    if (getrusage(RUSAGE_SELF,&usage)) return 3;
    printf("{\"mode\":\"%s\",\"budget_us\":%ld,\"entry_wall_ns\":%llu,\"finish_wall_ns\":%llu,\"entry_cpu_ns\":%llu,\"finish_cpu_ns\":%llu,\"target_pid\":%ld,\"cpu_count\":%d,\"cpu\":%d,\"current_cpu\":%d,\"self_rusage\":{\"user_sec\":%ld,\"user_usec\":%ld,\"system_sec\":%ld,\"system_usec\":%ld,\"minor_faults\":%ld,\"major_faults\":%ld,\"block_inputs\":%ld,\"block_outputs\":%ld,\"voluntary_switches\":%ld,\"involuntary_switches\":%ld}}\n",
        argv[1],budget,ns(entered),ns(finished),ns(cpu_entered),ns(cpu_finished),(long)getpid(),count,cpu,current,
        usage.ru_utime.tv_sec,usage.ru_utime.tv_usec,usage.ru_stime.tv_sec,usage.ru_stime.tv_usec,
        usage.ru_minflt,usage.ru_majflt,usage.ru_inblock,usage.ru_oublock,usage.ru_nvcsw,usage.ru_nivcsw);
    return 0;
}
