#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <sched.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/resource.h>
#include <time.h>
#include <unistd.h>
static unsigned long long ns(struct timespec t) { return (unsigned long long)t.tv_sec*1000000000ULL+(unsigned long long)t.tv_nsec; }
static int write_all(int fd,const char *data,size_t size) {
    while (size) { ssize_t n=write(fd,data,size);if(n<0 && errno==EINTR)continue;if(n<=0)return -1;data+=n;size-=(size_t)n; }return 0;
}
int main(int argc,char **argv) {
    struct timespec entered,finished;
    struct rusage u,v;
    if (clock_gettime(CLOCK_MONOTONIC,&entered) || getrusage(RUSAGE_SELF,&u)) return 3;
    if (argc!=2) return 2;
    char *end=NULL;long requested=strtol(argv[1],&end,10);
    if (!end || *end || (requested!=544 && requested!=169180)) return 2;
    cpu_set_t mask;CPU_ZERO(&mask);
    if (sched_getaffinity(0,sizeof(mask),&mask)) return 3;
    int count=CPU_COUNT(&mask),cpu=-1,current=sched_getcpu();
    for (int i=0;i<CPU_SETSIZE;++i)if(CPU_ISSET(i,&mask)){cpu=i;break;}
    if(count!=1 || current<0 || current!=cpu)return 3;
    char buffer[8192];memset(buffer,0x5A,sizeof(buffer));
    int fd=open("report.bin",O_CREAT|O_EXCL|O_WRONLY,0600);
    if(fd<0)return 3;
    size_t written=0;
    while(written<(size_t)requested){size_t chunk=(size_t)requested-written;if(chunk>sizeof(buffer))chunk=sizeof(buffer);if(write_all(fd,buffer,chunk)){(void)close(fd);return 3;}written+=chunk;}
    if(close(fd))return 3;
    if(getrusage(RUSAGE_SELF,&v) || clock_gettime(CLOCK_MONOTONIC,&finished))return 3;
    char line[2048];
    int size=snprintf(line,sizeof(line),"{\"pid\":%ld,\"cpu_count\":%d,\"cpu\":%d,\"current_cpu\":%d,\"requested_bytes\":%ld,\"written_bytes\":%zu,\"entry_wall_ns\":%llu,\"finish_wall_ns\":%llu,\"U\":{\"user_sec\":%ld,\"user_usec\":%ld,\"system_sec\":%ld,\"system_usec\":%ld,\"minor_faults\":%ld,\"major_faults\":%ld,\"block_inputs\":%ld,\"block_outputs\":%ld,\"voluntary_switches\":%ld,\"involuntary_switches\":%ld},\"V\":{\"user_sec\":%ld,\"user_usec\":%ld,\"system_sec\":%ld,\"system_usec\":%ld,\"minor_faults\":%ld,\"major_faults\":%ld,\"block_inputs\":%ld,\"block_outputs\":%ld,\"voluntary_switches\":%ld,\"involuntary_switches\":%ld}}\n",
        (long)getpid(),count,cpu,current,requested,written,ns(entered),ns(finished),
        (long)u.ru_utime.tv_sec,(long)u.ru_utime.tv_usec,(long)u.ru_stime.tv_sec,(long)u.ru_stime.tv_usec,u.ru_minflt,u.ru_majflt,u.ru_inblock,u.ru_oublock,u.ru_nvcsw,u.ru_nivcsw,
        (long)v.ru_utime.tv_sec,(long)v.ru_utime.tv_usec,(long)v.ru_stime.tv_sec,(long)v.ru_stime.tv_usec,v.ru_minflt,v.ru_majflt,v.ru_inblock,v.ru_oublock,v.ru_nvcsw,v.ru_nivcsw);
    if(size<=0 || size>=(int)sizeof(line) || write_all(1,line,(size_t)size))return 3;
    return 0;
}
