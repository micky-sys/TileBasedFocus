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
};
struct Record {
  uint64_t readUs,ticks;
  uint32_t sequence;
  vitality::Sample sample;
  bool timestampAnomaly;
};
struct Clock {
  bool have=false;
  uint16_t previous=0;
  uint64_t ticks=0;
  bool add(uint16_t stamp) {
    uint16_t delta=uint16_t(stamp-previous);
    // 16 kHz: about 58-59 internal-clock ticks/sample. Reject duplicates,
    // approximately doubled gaps, and a sensor accidentally still running 8 kHz.
    bool bad=have && (delta<40 || delta>80);
    if(have)ticks+=delta;
    previous=stamp;have=true;return bad;
  }
};
inline int format(char *out,size_t capacity,const Record &r) {
  const auto &s=r.sample;
  return snprintf(out,capacity,"%lu,%llu,%llu,%u,%d,%d,%d,%d,%d,%d,%d,%u,%u,%u\n",
    (unsigned long)r.sequence,(unsigned long long)r.readUs,
    (unsigned long long)r.ticks,s.timestamp,s.axes[0],s.axes[1],s.axes[2],
    s.axes[3],s.axes[4],s.axes[5],s.temperature,unsigned(s.valid),unsigned(r.timestampAnomaly),
    unsigned(s.temperatureValid));
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
