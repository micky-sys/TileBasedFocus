#pragma once
#include <stdint.h>
#include <string.h>

namespace xp {
// Portable state machine. One short hardware operation per step, no delays here.
// IO: prepare(sensor), write(sensor,hz,bytes), read(sensor,hz,bytes), restore(sensor).
template<class IO> class SpiTest {
public:
  static constexpr uint32_t rates[4]={4000000,8000000,12000000,16000000};
  static constexpr uint32_t defaultRounds=100000;
  struct Result {
    uint32_t rounds=0, badBytes=0, badBits=0, accessErrors=0;
    uint32_t firstRound=0;
    uint8_t firstByte=0, expected=0, actual=0;
    bool started=false, finished=false, usable=false;
  };
  enum Phase { NONE, PREPARE, WRITE, READ, RESTORE, DONE };
  Result results[4][4]{};
  uint32_t target=defaultRounds, round=0;
  uint8_t rate=0, sensor=0, restoreFailed=0;
  bool cancelled=false;
  Phase phase=NONE;
  bool active() const { return phase!=NONE && phase!=DONE; }
  bool available() const { return phase!=NONE; }
  bool done() const { return phase==DONE; }
  bool start(uint32_t count=defaultRounds) {
    if(active() || !count)return false;
    for(auto &group:results)for(auto &r:group)r=Result{};
    target=count;round=0;rate=sensor=restoreFailed=0;cancelled=false;phase=PREPARE;
    return true;
  }
  void cancel() { if(active() && phase!=RESTORE){cancelled=true;sensor=0;phase=RESTORE;} }
  bool failed() const {
    if(restoreFailed)return true;
    for(auto &group:results)for(auto &r:group)
      if(r.accessErrors || r.badBytes)return true;
    return false;
  }
  bool passed() const {
    if(!done() || cancelled || failed())return false;
    for(auto &group:results)for(auto &r:group)
      if(!r.finished || r.rounds!=target)return false;
    return true;
  }
  static void pattern(uint32_t iteration,uint8_t device,uint8_t *out) {
    // Distinct per-device/per-byte salts catch aliased CS lines and byte shifts.
    // Complementary, alternating, walking-bit, then deterministic random patterns.
    uint32_t rng=0x9e3779b9u ^ (iteration*0x85ebca6bu) ^ (uint32_t(device+1)*0xc2b2ae35u);
    for(uint8_t j=0;j<9;++j){
      uint8_t k=iteration%40, value;
      if(k==0)value=0;else if(k==1)value=255;else if(k==2)value=0xaa;else if(k==3)value=0x55;
      else if(k<12)value=uint8_t(1u<<(k-4));
      else if(k<20)value=uint8_t(~(1u<<(k-12)));
      else {rng^=rng<<13;rng^=rng>>17;rng^=rng<<5;value=uint8_t(rng);}
      out[j]=value ^ uint8_t(device*53+j*29) ^ uint8_t(iteration/40);
    }
  }
  void step(IO &io) {
    if(!active())return;
    if(phase==RESTORE){
      if(!io.restore(sensor))restoreFailed|=uint8_t(1u<<sensor);
      if(++sensor==4){sensor=0;phase=DONE;}
      return;
    }
    auto &r=results[rate][sensor];
    if(phase==PREPARE){
      r.started=true;r.usable=io.prepare(sensor);
      if(!r.usable)++r.accessErrors;
      if(++sensor==4){sensor=0;phase=WRITE;
        bool any=false;for(auto &x:results[rate])any|=x.usable;
        if(!any)finishRate();
      }
      return;
    }
    uint8_t expected[9], actual[9]={};pattern(round,sensor,expected);
    if(r.usable){
      bool ok=phase==WRITE?io.write(sensor,rates[rate],expected):io.read(sensor,rates[rate],actual);
      if(!ok){++r.accessErrors;r.usable=false;}
      else if(phase==READ){
        ++r.rounds;
        for(uint8_t j=0;j<9;++j)if(actual[j]!=expected[j]){
          if(!r.badBytes){r.firstRound=round;r.firstByte=j;r.expected=expected[j];r.actual=actual[j];}
          ++r.badBytes;uint8_t diff=actual[j]^expected[j];
          while(diff){r.badBits+=diff&1;diff>>=1;}
        }
      }
    }
    if(++sensor==4){sensor=0;
      if(phase==WRITE)phase=READ;
      else if(++round==target)finishRate();
      else {phase=WRITE;bool any=false;for(auto &x:results[rate])any|=x.usable;if(!any)finishRate();}
    }
  }
private:
  void finishRate(){
    for(auto &r:results[rate])r.finished=true;
    round=0;sensor=0;
    if(++rate==4){rate=3;phase=RESTORE;}else phase=PREPARE;
  }
};
}
