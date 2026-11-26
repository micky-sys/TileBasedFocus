#pragma once
#include <stdint.h>
#include <stddef.h>
#include <string.h>

namespace vitality {
// Main-loop-only text queue. Never wait for a slow/disconnected serial consumer.
template<size_t N> class TextQueue {
  uint8_t bytes[N]{};
  size_t head=0,tail=0,used=0;
public:
  uint32_t dropped=0;
  bool line(const char *text){
    size_t n=strlen(text);
    if(n+2>N-used){++dropped;return false;}
    for(size_t i=0;i<n;++i)push(uint8_t(text[i]));
    push('\r');push('\n');return true;
  }
  size_t count() const{return used;}
  const uint8_t *data() const{return bytes+tail;}
  size_t contiguous() const{return used<N-tail?used:N-tail;}
  void consume(size_t n){tail=(tail+n)%N;used-=n;}
  void clear(){head=tail=used=0;}
private:
  void push(uint8_t v){bytes[head]=v;head=(head+1)%N;++used;}
};
struct Sample {
  int16_t axes[6]{};
  int8_t temperature=0;
  uint16_t timestamp=0;
  bool valid=false;
};
inline uint16_t be16(const uint8_t *p){return (uint16_t(p[0])<<8)|p[1];}
inline bool decode(const uint8_t *p,Sample &s){
  // 16-byte accel+gyro packet with ODR timestamp, no FSYNC and no high resolution.
  // Low header bits may announce the initial ODR change.
  if((p[0]&0xfc)!=0x68)return false;
  s.valid=true;
  for(unsigned i=0;i<6;++i){s.axes[i]=int16_t(be16(p+1+2*i));if(s.axes[i]==INT16_MIN)s.valid=false;}
  s.temperature=int8_t(p[13]);s.timestamp=be16(p+14);
  if(s.temperature==INT8_MIN)s.valid=false;
  return true;
}
struct TimestampCheck {
  bool have=false;
  uint16_t previous=0;
  uint32_t anomalies=0;
  void add(uint16_t stamp){
    // 8 kHz: ~117 ticks/sample with the internal clock (~1.067 us/tick).
    // Broad screening interval includes nominal 1 us interpretation; excludes
    // duplicate timestamps and approximately double-length missing-sample gaps.
    if(have){uint16_t delta=uint16_t(stamp-previous);if(delta<80 || delta>160)++anomalies;}
    previous=stamp;have=true;
  }
};
}
