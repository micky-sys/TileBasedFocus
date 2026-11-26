#include "../DualCsvLogger/LoggerCore.h"
#include <cassert>
#include <string>
#include <cstdio>
#include <random>
struct Boot {
  bool sent=true,ack=true;std::string actions;
  bool dump(){actions+="D";return sent && ack;}
  bool erase(){actions+="E";return true;}
  bool create(){actions+="C";return true;}
};
int main(){
  assert(csvlog::ackMatches("ACK_LOG 40703177 E13150E2",40703177,0xE13150E2));
  for(const char *s:{"ACK_LOG 40703176 E13150E2","ACK_LOG 40703177 E13150E3","ACK_LOG","NEW","ACK_LOG 40703177 E13150E2 trailing"})
    assert(!csvlog::ackMatches(s,40703177,0xE13150E2));
  Boot b;b.ack=false;assert(csvlog::prepare(b)==csvlog::BootResult::DumpFailed && b.actions=="D");
  Boot good;assert(csvlog::prepare(good)==csvlog::BootResult::Ready && good.actions=="DEC");
  csvlog::Clock clocks[2];
  assert(!clocks[0].add(65520));assert(!clocks[1].add(120));
  assert(!clocks[0].add(43));assert(!clocks[1].add(178));
  assert(clocks[0].ticks==59 && clocks[1].ticks==58);
  assert(!clocks[0].add(21,true) && clocks[0].ticks==59);
  assert(!clocks[0].add(160) && clocks[0].ticks==176); // two ODR periods, delta not unwrapped
  assert(!clocks[0].add(219) && clocks[0].ticks==235);
  csvlog::Clock wrap;assert(!wrap.add(65510));assert(!wrap.add(30,true));
  assert(!wrap.add(91) && wrap.ticks==117);
  assert(wrap.add(200,true)); // impossible FSYNC->next ODR delay
  uint8_t packet[16]={};vitality::Sample decoded;
  packet[0]=0x6c;packet[15]=22;
  assert(vitality::decode(packet,decoded) && decoded.fsync && decoded.timestamp==22);
  packet[0]=0x68;assert(vitality::decode(packet,decoded) && !decoded.fsync);
  packet[0]=0x64;assert(!vitality::decode(packet,decoded));
  packet[0]=0x78;assert(!vitality::decode(packet,decoded));
  csvlog::Ring<csvlog::Record,4> q;
  for(unsigned n=0;n<4;++n){csvlog::Record r{};r.sensorId=n%2?3:1;r.sequence=n/2;r.sample.temperatureValid=true;assert(q.push(r));}
  csvlog::Record r{};assert(!q.push(r));
  for(unsigned n=0;n<4;++n){assert(q.pop(r));assert(r.sensorId==(n%2?3:1));assert(r.sequence==n/2);
    char line[192];int len=csvlog::format(line,sizeof(line),r);assert(len>0 && len<192);
    assert(std::string(line).substr(len-6)==(n%2?"1,3,0\n":"1,1,0\n"));}
  q.clear();assert(q.count()==0 && q.push(r));
  // Keep every CSV byte identical to the old reference, including long-running
  // 64-bit clocks, negative sentinel values and zero-valued fields.
  std::mt19937_64 random(42688);
  for(unsigned test=0;test<10000;++test){
    csvlog::Record value{};value.sequence=uint32_t(random());value.readUs=random();value.ticks=random();
    if(test==0){value.readUs=UINT64_MAX;value.ticks=0;}
    for(auto &axis:value.sample.axes)axis=int16_t(random());
    value.sample.temperature=int8_t(random());value.sample.timestamp=uint16_t(random());
    value.sample.valid=random()&1;value.sample.temperatureValid=random()&1;
    value.timestampAnomaly=random()&1;value.sensorId=test%2?1:3;
    value.syncId=uint32_t(random());
    char actual[192],expected[192];const auto &s=value.sample;
    int expectedLength=snprintf(expected,sizeof(expected),"%lu,%llu,%llu,%u,%d,%d,%d,%d,%d,%d,%d,%u,%u,%u,%u,%lu\n",
      (unsigned long)value.sequence,(unsigned long long)value.readUs,(unsigned long long)value.ticks,s.timestamp,
      s.axes[0],s.axes[1],s.axes[2],s.axes[3],s.axes[4],s.axes[5],s.temperature,
      unsigned(s.valid),unsigned(value.timestampAnomaly),unsigned(s.temperatureValid),unsigned(value.sensorId),(unsigned long)value.syncId);
    assert(csvlog::format(actual,sizeof(actual),value)==expectedLength && !strcmp(actual,expected));
    if(test<10){
      for(size_t capacity=0;capacity<=size_t(expectedLength)+1;++capacity){
        memset(actual,'!',sizeof(actual));int result=csvlog::format(actual,capacity,value);
        assert(actual[capacity]=='!');
        assert(capacity>size_t(expectedLength)?result==expectedLength:result<0);
      }
    }
  }
  puts("Dual CSV tests passed: per-sensor clocks/sequences, bounded shared queue, sensor IDs, ACK gate and destructive order.");
}
