#define _GNU_SOURCE
#include <sched.h>
#include <stdio.h>
#include <stdlib.h>
#include <time.h>
#include <unistd.h>
static unsigned long long ns(struct timespec value) { return (unsigned long long)value.tv_sec*1000000000ULL+(unsigned long long)value.tv_nsec; }
int main(int argc,char **argv) {
    struct timespec entered, cpu_entered, cpu_finished, finished;
    if (clock_gettime(CLOCK_MONOTONIC,&entered) || clock_gettime(CLOCK_THREAD_CPUTIME_ID,&cpu_entered)) return 3;
    if (argc!=2) return 2;
    char *end=NULL; long budget=strtol(argv[1],&end,10);
    if (!end || *end || (budget!=0 && budget!=500)) return 2;
    cpu_set_t mask; CPU_ZERO(&mask);
    if (sched_getaffinity(0,sizeof(mask),&mask)) return 3;
    int count=CPU_COUNT(&mask), cpu=-1;
    for (int i=0;i<CPU_SETSIZE;++i) if (CPU_ISSET(i,&mask)) { cpu=i; break; }
    do {
        if (clock_gettime(CLOCK_THREAD_CPUTIME_ID,&cpu_finished)) return 3;
    } while (budget && ns(cpu_finished)-ns(cpu_entered)<(unsigned long long)budget*1000ULL);
    int current=sched_getcpu();
    if (current<0 || clock_gettime(CLOCK_THREAD_CPUTIME_ID,&cpu_finished) || clock_gettime(CLOCK_MONOTONIC,&finished)) return 3;
    printf("{\"budget_us\":%ld,\"entry_wall_ns\":%llu,\"finish_wall_ns\":%llu,\"entry_cpu_ns\":%llu,\"finish_cpu_ns\":%llu,\"target_pid\":%ld,\"cpu_count\":%d,\"cpu\":%d,\"current_cpu\":%d}\n",
        budget,ns(entered),ns(finished),ns(cpu_entered),ns(cpu_finished),(long)getpid(),count,cpu,current);
    return 0;
}
