#pragma once
#include <atomic>
#include <stdint.h>
#include <stddef.h>
#include <stdio.h>
#include "VitalityCore.h"

namespace csvlog {
// One interrupt producer, one main-loop consumer. Never overwrite unread data.
template<class T, uint32_t N> class Ring {
  static_assert((N & (N-1))==0, "Power of two required");
  T data[N];
  std::atomic<uint32_t> wr{0},rd{0};
public:
  bool push(const T &v) {
    uint32_t w=wr.load(std::memory_order_relaxed);
    if(w-rd.load(std::memory_order_acquire)==N)return false;
    data[w&(N-1)]=v;wr.store(w+1,std::memory_order_release);return true;
  }
  bool pop(T &v) {
    uint32_t r=rd.load(std::memory_order_relaxed);
    if(r==wr.load(std::memory_order_acquire))return false;
    v=data[r&(N-1)];rd.store(r+1,std::memory_order_release);return true;
  }
  uint32_t count() const {return wr.load()-rd.load();}
  void clear(){wr.store(0);rd.store(0);} // Producer MUST be stopped.
};
struct Record {
  uint64_t readUs,ticks;
  uint32_t sequence;
  uint32_t syncId=0;
  vitality::Sample sample;
  bool timestampAnomaly;
  uint8_t sensorId;
};
static_assert(sizeof(Record)==48, "RAM queue budget changed");
struct SyncEvent {uint64_t beforeUs;uint32_t id;uint16_t bracketUs;uint8_t sensorId;};
inline bool ackMatches(const char *line,uint64_t size,uint32_t crc) {
  char expected[80];
  snprintf(expected,sizeof(expected),"ACK_LOG %llu %08lX",(unsigned long long)size,(unsigned long)crc);
  return strcmp(line,expected)==0;
}
struct Clock {
  bool have=false;
  uint16_t previous=0;
  uint64_t ticks=0;
  bool skipped=false;
  bool add(uint16_t stamp,bool fsync=false) {
    // A marked FIFO packet replaces the absolute timestamp with FSYNC->ODR
    // delta. Keep the preceding absolute ticks; the host resolves this one
    // packet from its two neighbouring ODR stamps. Never unwrap the delta.
    if(fsync){bool bad=!have || skipped || stamp>80;skipped=true;return bad;}
    uint16_t delta=uint16_t(stamp-previous);
    // 16 kHz: about 58-59 internal-clock ticks/sample. Reject duplicates,
    // approximately doubled gaps, and a sensor accidentally still running 8 kHz.
    bool bad=have && (delta<(skipped?80:40) || delta>(skipped?160:80));
    if(have)ticks+=delta;
    previous=stamp;have=true;skipped=false;return bad;
  }
};
// Fixed integer-only schema: avoid printf's generic variadic parser on the
// 32,000+ rows/s acquisition path. Prefer 32-bit arithmetic on Cortex-M7.
class CsvWriter {
  char *out;size_t capacity,used=0;
public:
  CsvWriter(char *p,size_t n):out(p),capacity(n){}
  void put(char c){if(used+1<capacity)out[used]=c;++used;}
  void u32(uint32_t value){
    char digits[10];unsigned n=0;
    do {digits[n++]=char('0'+value%10);value/=10;}while(value);
    while(n)put(digits[--n]);
  }
  void u64(uint64_t value){
    if(value<=UINT32_MAX){u32(uint32_t(value));return;}
    // Only the rare high part requires 64-bit division; low group is padded.
    uint64_t high=value/1000000000ULL;
    uint32_t low=uint32_t(value-high*1000000000ULL);
    u64(high);
    char digits[9];for(unsigned i=0;i<9;++i){digits[i]=char('0'+low%10);low/=10;}
    for(unsigned i=9;i>0;--i)put(digits[i-1]);
  }
  void signed32(int32_t value){
    if(value<0){put('-');u32(0u-uint32_t(value));}else u32(uint32_t(value));
  }
  int finish(){
    if(capacity)out[used<capacity?used:capacity-1]=0;
    return used<capacity?int(used):-1;
  }
};
inline int format(char *out,size_t capacity,const Record &r) {
  CsvWriter w(out,capacity);const auto &s=r.sample;
  w.u32(r.sequence);w.put(',');w.u64(r.readUs);w.put(',');w.u64(r.ticks);
  w.put(',');w.u32(s.timestamp);
  for(unsigned i=0;i<6;++i){w.put(',');w.signed32(s.axes[i]);}
  w.put(',');w.signed32(s.temperature);w.put(',');w.u32(s.valid);
  w.put(',');w.u32(r.timestampAnomaly);w.put(',');w.u32(s.temperatureValid);
  w.put(',');w.u32(r.sensorId);w.put(',');w.u32(r.syncId);w.put('\n');return w.finish();
}
inline uint32_t crcStep(uint32_t crc,const uint8_t *data,size_t n) {
  while(n--){crc^=*data++;for(int b=0;b<8;++b)crc=(crc>>1)^((0u-(crc&1))&0xedb88320u);}
  return crc;
}
// A short read is fine; EOF before the advertised size is not. Sink must accept
// every byte or return false. No destructive operation occurs in this helper.
template<class Reader,class Sink>
bool copyExactly(Reader &source,Sink &sink,uint64_t size,uint32_t &crc) {
  uint8_t buffer[1024];crc=0xffffffffu;
  while(size){
    size_t want=size<sizeof(buffer)?size:sizeof(buffer);
    int got=source.read(buffer,want);
    if(got<=0 || size_t(got)>want || !sink.send(buffer,size_t(got)))return false;
    crc=crcStep(crc,buffer,size_t(got));size-=size_t(got);
  }
  crc=~crc;return true;
}
// Explicit destructive ordering, also exercised by native failure-injection tests.
enum class BootResult { DumpFailed, EraseFailed, CreateFailed, Ready };
template<class Operations> BootResult prepare(Operations &ops) {
  if(!ops.dump())return BootResult::DumpFailed;
  if(!ops.erase())return BootResult::EraseFailed;
  if(!ops.create())return BootResult::CreateFailed;
  return BootResult::Ready;
}
// File adapter: SdFat FsFile in firmware, in-memory filesystem in tests.
// Root itself is retained. Files are reopened for writing by remove(name).
template<class File> bool purge(File &directory,unsigned depth=0) {
  if(depth>16)return false;
  directory.rewind();
  File entry;
  while(entry.openNext(&directory)){
    if(entry.isDir()){
      if(!purge(entry,depth+1) || !entry.rmdir())return false;
    }else{
      char name[1024];size_t n=entry.getName(name,sizeof(name));
      if(!n || n>=sizeof(name)-1 || !entry.close() || !directory.remove(name))return false;
    }
  }
  return directory.getError()==0;
}
}
