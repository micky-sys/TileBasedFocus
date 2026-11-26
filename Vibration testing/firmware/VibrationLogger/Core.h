#pragma once
#include <atomic>
#include <stdint.h>
#include <stddef.h>
#include <string.h>

namespace xp {
constexpr uint32_t MAGIC = 0x31525058; // On disk: XPR1
constexpr size_t MAX_PAYLOAD = 400;
enum Type : uint8_t { META=1, FIFO=2, SYNC=3, EDGE=4, GPS=5, BURST=6, END=7, STATS=8 };
struct Record {
  uint64_t time;
  uint16_t size;
  uint8_t type, source;
  uint8_t data[MAX_PAYLOAD];
};
#pragma pack(push, 1)
struct Header {
  uint32_t magic;
  uint16_t size;
  uint8_t type, source;
  uint32_t sequence;
  uint64_t time;
  uint32_t crc;
};
#pragma pack(pop)
static_assert(sizeof(Header) == 24, "File format");
static_assert(sizeof(Record) == 416, "RAM budget");

// Exactly one producer and one consumer; independent queues for GPIO and IMU ISR.
template<class T, uint32_t N> class Ring {
  static_assert((N & (N-1)) == 0, "Power-of-two capacity");
  T items[N];
  std::atomic<uint32_t> wr{0}, rd{0};
public:
  bool push(const T &v) {
    const uint32_t w=wr.load(std::memory_order_relaxed);
    if (w-rd.load(std::memory_order_acquire) == N) return false;
    items[w & (N-1)] = v;
    wr.store(w+1, std::memory_order_release); return true;
  }
  bool pop(T &v) {
    const uint32_t r=rd.load(std::memory_order_relaxed);
    if (r==wr.load(std::memory_order_acquire)) return false;
    v=items[r & (N-1)]; rd.store(r+1, std::memory_order_release); return true;
  }
  uint32_t count() const { return wr.load()-rd.load(); }
  void clear() { wr.store(0); rd.store(0); } // Only while producer is stopped.
};
inline uint32_t crcStep(uint32_t crc, const void *p, size_t n) {
  const auto *b=static_cast<const uint8_t*>(p);
  while(n--) { crc^=*b++; for(int j=0;j<8;++j) crc=(crc>>1)^((0u-(crc&1))&0xedb88320u); }
  return crc;
}
inline uint32_t recordCRC(const Header &h, const void *payload) {
  return ~crcStep(crcStep(0xffffffffu, &h, 20), payload, h.size);
}
inline uint16_t be16(const uint8_t *p) { return uint16_t(p[0])<<8 | p[1]; }
inline void put32(uint8_t *p,uint32_t v) { memcpy(p,&v,4); }
inline int hex(char c) {
  if(c>='0'&&c<='9')return c-'0';
  if(c>='a'&&c<='f')return c-'a'+10;
  if(c>='A'&&c<='F')return c-'A'+10;
  return -1;
}
// Parse without strtok: empty fields, extra commas and non-hex checksums rejected.
inline bool command(char *line, char *&id, char *&verb) {
  size_t n=strlen(line);
  if(n<26 || n>244 || line[0]!='@' || line[n-3]!='*')return false;
  int a=hex(line[n-2]),b=hex(line[n-1]); if(a<0||b<0)return false;
  uint8_t crc=0; for(size_t i=1;i<n-3;++i)crc^=uint8_t(line[i]);
  if(crc!=((a<<4)|b) || strncmp(line+1,"XP,1,C,",7))return false;
  line[n-3]=0; id=line+8;
  char *comma=strchr(id,','); if(!comma || comma-id!=12)return false;
  for(char *p=id;p<comma;++p)if(!((*p>='0'&&*p<='9')||(*p>='a'&&*p<='f')))return false;
  *comma=0; verb=comma+1; if(!*verb)return false;
  for(char *p=verb;*p;++p)if(*p<'A'||*p>'Z')return false;
  return true;
}
struct Window {
  bool active=false;
  uint64_t first=0, begin=0, end=0;
  uint32_t merged=0;
  void trigger(uint64_t t, uint32_t pre, uint32_t post) {
    if(!active) { active=true; first=t; begin=t>pre?t-pre:0; end=t+post; }
    else { ++merged; if(t+post>end)end=t+post; }
  }
  void extend(uint64_t t,uint32_t post) { if(active && t+post>end)end=t+post; }
};
}
