#include "../VibrationLogger/SpiTest.h"
#include "../VibrationLogger/SpiTestIo.h"
#include <cassert>
#include <cstdio>
#include <cstring>

struct FakeIo {
  uint8_t values[4][9]{};
  unsigned prepares[4]{},restores[4]{},writes[4]{},reads[4]{};
  int missing=-1,broken=-1,restoreBad=-1,alias=-1;
  bool corrupt=false,writeFail=false;
  bool prepare(uint8_t s){++prepares[s];return s!=missing;}
  bool write(uint8_t s,uint32_t,const uint8_t *p){
    ++writes[s];if(writeFail && s==broken)return false;
    memcpy(values[s==alias?0:s],p,9);return true;
  }
  bool read(uint8_t s,uint32_t hz,uint8_t *p){
    ++reads[s];if(!writeFail && s==broken)return false;
    memcpy(p,values[s==alias?0:s],9);
    if(corrupt && s==2 && hz==12000000)p[4]^=0x81;
    return true;
  }
  bool restore(uint8_t s){++restores[s];memset(values[s],0,9);return s!=restoreBad;}
};
using Test=xp::SpiTest<FakeIo>;
static void finish(Test &t,FakeIo &io){
  unsigned budget=100000;
  while(t.active() && budget--)t.step(io);
  assert(budget && t.done());
  for(auto n:io.restores)assert(n==1);
}

// Emulate the device API, with bank, power, address and frequency assertions.
struct FakeDevice {
  uint32_t hz=16000000;
  uint8_t bank=0,offset[9]{};
  bool configureOk=true,dirtyReset=false,damageBank=false;
  unsigned configurations=0,patternWrites=0;
  void setClock(uint32_t v){hz=v;}
  bool configure(uint8_t){
    assert(hz==1000000);++configurations;bank=0;
    memset(offset,dirtyReset?1:0,9);return configureOk;
  }
  uint8_t get(uint8_t reg){assert(reg==0x76 && hz==1000000);return bank;}
  bool checked(uint8_t reg,uint8_t v){
    assert(hz==1000000);
    if(reg==0x76){bank=v;return true;}
    assert(bank==0 && (reg==0x4e || reg==0x16) && v==0);return true;
  }
  void set(uint8_t reg,uint8_t v){
    assert(bank==4 && reg>=0x77 && reg<=0x7f);
    assert(hz==4000000 || hz==8000000 || hz==12000000 || hz==16000000);
    ++patternWrites;offset[reg-0x77]=v;
    if(damageBank)bank=0; // Corrupt address changed bank: subsequent writes must stop.
  }
  void read(uint8_t reg,uint8_t *p,size_t n){
    assert(bank==4 && reg==0x77 && n==9);memcpy(p,offset,9);
  }
};

int main(){
  {
    Test t;FakeIo io;assert(!t.available() && !t.passed());assert(!t.start(0));
    assert(t.start(80));assert(!t.start(1));finish(t,io);assert(t.passed());
    for(auto &group:t.results)for(auto &r:group){assert(r.rounds==80 && r.finished);assert(!r.badBytes);}
    for(unsigned s=0;s<4;++s){assert(io.prepares[s]==4 && io.reads[s]==320 && io.writes[s]==320);}
    io=FakeIo{};assert(t.start(2));finish(t,io);assert(t.passed());
  }
  {
    Test t;FakeIo io;io.corrupt=true;t.start(7);finish(t,io);
    auto &r=t.results[2][2];assert(r.badBytes==7 && r.badBits==14 && r.rounds==7);
    assert(r.firstRound==0 && r.firstByte==4 && (r.expected^r.actual)==0x81);
    assert(t.failed() && !t.passed());assert(!t.results[3][2].badBytes);
  }
  {
    Test t;FakeIo io;io.alias=1;t.start(3);finish(t,io);
    for(auto &g:t.results)assert(g[0].badBytes>0);
    assert(!t.passed());
  }
  for(int error=0;error<3;++error){
    Test t;FakeIo io;
    if(error==0)io.missing=1;else {io.broken=1;io.writeFail=error==1;}
    t.start(5);finish(t,io);assert(!t.passed());
    for(auto &g:t.results){assert(g[1].accessErrors==1 && g[1].rounds==0);assert(g[0].rounds==5);}
  }
  // Abort at every point of a small test, including preparation and readback.
  for(unsigned stop=0;stop<80;++stop){
    Test t;FakeIo io;t.start(3);
    for(unsigned j=0;j<stop && t.active();++j)t.step(io);
    t.cancel();finish(t,io);assert(!t.passed() && t.cancelled);
  }
  {
    Test t;FakeIo io;io.restoreBad=3;t.start(1);finish(t,io);
    assert(t.restoreFailed==8 && t.failed() && !t.passed());
  }
  {
    FakeDevice devices[4];SpiTestIo<FakeDevice> io(devices,16000000);
    xp::SpiTest<SpiTestIo<FakeDevice>> t;t.start(2);
    while(t.active())t.step(io);
    assert(t.passed());
    for(auto &d:devices){assert(d.bank==0 && d.hz==16000000 && d.configurations==5);
      assert(d.patternWrites==4*2*9);for(auto b:d.offset)assert(!b);}
    uint8_t pattern[9]{},readback[9]{};
    assert(io.prepare(0));devices[0].damageBank=true;
    unsigned n=devices[0].patternWrites;
    assert(!io.write(0,16000000,pattern));assert(devices[0].patternWrites==n+1);
    assert(!io.read(0,16000000,readback));assert(io.restore(0));
    devices[1].dirtyReset=true;assert(!io.restore(1));assert(devices[1].bank==0 && devices[1].hz==16000000);
    devices[2].configureOk=false;assert(!io.prepare(2));assert(!io.restore(2));
    assert(devices[2].bank==0 && devices[2].hz==16000000);
  }
  puts("PASS: SPI rates, 4 sensors, patterns, bit errors, aliased CS, missing devices, abort, cleanup and bank guards");
}
