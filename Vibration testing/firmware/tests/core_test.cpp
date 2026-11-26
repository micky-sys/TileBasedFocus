#include "../VibrationLogger/Core.h"
#include <cassert>
#include <cstdio>
#include <string>
#include <thread>
#include <fstream>
#include <vector>

static std::string frame(std::string payload) {
  unsigned char sum=0;for(char c:payload)sum^=c;
  char suffix[8];snprintf(suffix,sizeof(suffix),"*%02X",sum);return "@"+payload+suffix;
}
static bool accepts(std::string s) {char *id,*verb;return xp::command(s.data(),id,verb);}
static void emit(std::ofstream &f,uint32_t &seq,uint8_t type,uint8_t source,uint64_t time,const void *p,size_t size){
  xp::Header h{xp::MAGIC,uint16_t(size),type,source,seq++,time,0};h.crc=xp::recordCRC(h,p);
  f.write(reinterpret_cast<const char*>(&h),sizeof(h));f.write(static_cast<const char*>(p),size);
}
int main(int argc,char **argv) {
  assert(argc==2);
  for(auto verb:{"ARM","START","STOP","STATUS","HEALTH","SPITEST","SPITESTRESULT"})assert(accepts(frame(std::string("XP,1,C,0123456789ab,")+verb)));
  assert(!accepts(frame("XP,1,C,,0123456789ab,ARM")));
  assert(!accepts(frame("XP,2,C,0123456789ab,ARM")));
  assert(!accepts(frame("XP,1,C,0123456789ab,ARM,EXTRA")));
  assert(!accepts(frame("XP,1,C,0123456789ab,")));
  auto corrupt=frame("XP,1,C,0123456789ab,ARM");corrupt[10]='f';assert(!accepts(corrupt));
  assert(!accepts("@XP,1,C,0123456789ab,ARM*G0"));
  xp::Ring<uint32_t,8> small;uint32_t v;
  for(uint32_t i=0;i<8;++i)assert(small.push(i));
  assert(!small.push(99));
  for(uint32_t i=0;i<8;++i){assert(small.pop(v));assert(v==i);}assert(!small.pop(v));
  xp::Ring<uint32_t,1024> concurrent;
  std::thread producer([&]{for(uint32_t i=0;i<200000;++i)while(!concurrent.push(i))std::this_thread::yield();});
  for(uint32_t i=0;i<200000;++i){while(!concurrent.pop(v))std::this_thread::yield();assert(v==i);}producer.join();
  xp::Window w;w.trigger(1000,20000,20000);assert(w.begin==0&&w.end==21000);
  w.trigger(15000,20000,20000);assert(w.first==1000&&w.end==35000&&w.merged==1);
  w.extend(40000,20000);assert(w.end==60000);
  const char *check="123456789";assert(~xp::crcStep(0xffffffff,check,9)==0xcbf43926);
  std::ofstream f(argv[1],std::ios::binary);assert(f);uint32_t seq=0;
  const char *meta="{\"session\":1,\"segment\":0,\"mode\":\"continuous\"}";
  emit(f,seq,xp::META,255,0,meta,strlen(meta));
  for(uint32_t group=0;group<3;++group){
    for(uint8_t s=0;s<4;++s){
      uint32_t marker[3]={group+1,0,group*160};
      emit(f,seq,xp::SYNC,s,10000+group*10000,marker,sizeof(marker));
      for(uint32_t first=0;first<160;first+=16){
        uint8_t data[8+256]={};xp::put32(data,group*160+first);xp::put32(data+4,16);
        for(uint32_t j=0;j<16;++j){
          auto *p=data+8+j*16;uint32_t index=group*160+first+j;
          p[0]=first==0&&j==0?0x6c:0x68;
          p[5]=8; // Z acceleration = 2048 counts = 1 g.
          p[7]=0x10; // X gyro = 4096 counts = 250 dps.
          uint16_t stamp=(first==0&&j==0)?30:uint16_t((index*1875ull+16)/32); // 58.59375 tick/sample.
          p[14]=stamp>>8;p[15]=stamp;
        }
        emit(f,seq,xp::FIFO,s,11000+group*10000+first*63,data,sizeof(data));
      }
    }
  }
  const char *end="{\"clean\":true}";emit(f,seq,xp::END,255,41000,end,strlen(end));
  puts("Core tests passed: parser rejection, bounded queue/concurrency, burst windows, CRC, binary fixture.");
}
