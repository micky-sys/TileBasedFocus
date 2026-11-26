#include "../HubVitality/VitalityCore.h"
#include <cassert>
#include <cstdio>
#include <string>

int main(){
  vitality::TextQueue<16> q;
  assert(q.line("12345678"));assert(q.count()==10);
  // A full-queue rejection must drop the entire line, not corrupt its predecessor.
  assert(!q.line("ABCDEFG"));assert(q.dropped==1 && q.count()==10);
  std::string first(reinterpret_cast<const char*>(q.data()),6);q.consume(6);
  assert(q.line("abcd")); // Cross the physical end of the ring.
  while(q.count()){
    first.append(reinterpret_cast<const char*>(q.data()),q.contiguous());q.consume(q.contiguous());
  }
  assert(first=="12345678\r\nabcd\r\n");assert(q.line("again"));q.clear();assert(q.count()==0);
  uint8_t packet[16]={0x68,0x08,0,0xf8,0,0,0,0x10,0,0xf0,0,0,0,0xfe,0xff,0xf0};
  vitality::Sample sample;
  assert(vitality::decode(packet,sample) && sample.valid);
  assert(sample.axes[0]==2048 && sample.axes[1]==-2048);
  assert(sample.axes[3]==4096 && sample.axes[4]==-4096);
  assert(sample.temperature==-2 && sample.timestamp==65520);
  packet[0]=0x6b;assert(vitality::decode(packet,sample)); // initial ODR flags allowed
  for(uint8_t header:{0x80,0x48,0x78,0x6c,0x60}){
    packet[0]=header;assert(!vitality::decode(packet,sample));
  }
  packet[0]=0x68;packet[1]=0x80;packet[2]=0;
  assert(vitality::decode(packet,sample) && !sample.valid);
  packet[1]=8;packet[13]=0x80;assert(vitality::decode(packet,sample) && !sample.valid);
  vitality::TimestampCheck t;t.add(65520);t.add(101);t.add(219);assert(t.anomalies==0);
  t.add(219);assert(t.anomalies==1); // duplicate
  t.add(453);assert(t.anomalies==2); // one missing sample
  t.add(570);assert(t.anomalies==2);
  puts("PASS: nonblocking output queue wrap/drop atomicity, FIFO format/signs/sentinels, timestamp wrap/duplicate/gap");
}
