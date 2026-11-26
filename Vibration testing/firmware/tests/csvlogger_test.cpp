#include "../ResetCsvLogger/LoggerCore.h"
#include <cassert>
#include <string>
#include <vector>
#include <algorithm>
#include <thread>
#include <iostream>

struct Reader {
  std::string bytes;size_t offset=0,limit=7;int failAt=-1;
  int read(uint8_t *out,size_t n){
    if(failAt>=0 && offset>=size_t(failAt))return -1;
    n=std::min(n,std::min(limit,bytes.size()-offset));
    memcpy(out,bytes.data()+offset,n);offset+=n;return int(n);
  }
};
struct Sink {
  std::string bytes;bool fail=false;
  bool send(const uint8_t *p,size_t n){if(fail)return false;bytes.append((const char*)p,n);return true;}
};
struct Boot {
  std::string calls;bool d=true,e=true,c=true;
  bool dump(){calls+='D';return d;}
  bool erase(){calls+='E';return e;}
  bool create(){calls+='C';return c;}
};
struct Node {
  std::string name;bool directory=false,removed=false,removeFails=false,readFails=false;
  std::vector<Node*> children;
  Node(std::string n="",bool dir=false):name(n),directory(dir){}
};
// Mock the subset of SdFat used by purge. No actual filesystem is deleted.
struct File {
  Node *node=nullptr;size_t cursor=0;bool error=false;
  File()=default;explicit File(Node *n):node(n){}
  void rewind(){cursor=0;}
  bool openNext(File *parent){
    if(parent->node->readFails){parent->error=true;return false;}
    while(parent->cursor<parent->node->children.size()){
      Node *n=parent->node->children[parent->cursor++];
      if(!n->removed){node=n;cursor=0;error=false;return true;}
    }
    return false;
  }
  bool isDir(){return node->directory;}
  size_t getName(char *out,size_t capacity){
    size_t n=std::min(capacity-1,node->name.size());memcpy(out,node->name.data(),n);out[n]=0;return n;
  }
  bool close(){node=nullptr;return true;}
  bool remove(const char *name){
    for(Node *n:node->children)if(n->name==name && !n->removed){
      if(n->directory || n->removeFails)return false;
      n->removed=true;return true;
    }
    return false;
  }
  bool rmdir(){
    if(node->removeFails)return false;
    for(Node *n:node->children)if(!n->removed)return false;
    node->removed=true;node=nullptr;return true;
  }
  int getError(){return error;}
};
int main(){
  using csvlog::BootResult;
  {Boot b;assert(csvlog::prepare(b)==BootResult::Ready && b.calls=="DEC");}
  {Boot b;b.d=false;assert(csvlog::prepare(b)==BootResult::DumpFailed && b.calls=="D");}
  {Boot b;b.e=false;assert(csvlog::prepare(b)==BootResult::EraseFailed && b.calls=="DE");}
  {Boot b;b.c=false;assert(csvlog::prepare(b)==BootResult::CreateFailed && b.calls=="DEC");}
  {Reader r{"123456789"};Sink s;uint32_t crc;
    assert(csvlog::copyExactly(r,s,9,crc));assert(s.bytes==r.bytes && crc==0xcbf43926);}
  {Reader r{""};Sink s;uint32_t crc;assert(csvlog::copyExactly(r,s,0,crc) && crc==0);}
  {Reader r{"short"};Sink s;uint32_t crc;assert(!csvlog::copyExactly(r,s,10,crc));}
  {Reader r{"123456789"};r.failAt=7;Sink s;uint32_t crc;assert(!csvlog::copyExactly(r,s,9,crc));}
  {Reader r{"123456789"};Sink s;s.fail=true;uint32_t crc;assert(!csvlog::copyExactly(r,s,9,crc));}
  {Node a{"log.csv"},b{"Other file.txt"},c{"hidden-file"},dir{"sub",true},root{"/",true};
    dir.children={&c};root.children={&a,&dir,&b};File f(&root);
    assert(csvlog::purge(f));assert(a.removed && b.removed && c.removed && dir.removed && !root.removed);}
  {Node a{"log.csv"},b{"readonly"};b.removeFails=true;Node root{"/",true};root.children={&b,&a};
    File f(&root);assert(!csvlog::purge(f));assert(!a.removed && !b.removed);}
  {Node root{"/",true};root.readFails=true;File f(&root);assert(!csvlog::purge(f));}
  {Node dirs[19];for(int i=0;i<19;++i){dirs[i].directory=true;if(i<18)dirs[i].children={&dirs[i+1]};}
    File f(&dirs[0]);assert(!csvlog::purge(f));}
  {csvlog::Clock c;assert(!c.add(65500));assert(!c.add(22));assert(c.ticks==58);
    assert(!c.add(81));assert(c.ticks==117); // alternating 58/59 ticks, including rollover
    assert(c.add(81));assert(c.add(198));assert(c.ticks==234); // duplicate and 8 kHz/gap
    assert(c.add(237));assert(!c.add(277));assert(!c.add(357));assert(c.add(438));} // boundaries
  {csvlog::Record r{};r.sequence=12;r.readUs=4294967299ULL;r.ticks=8000000000ULL;
    r.sample.axes[0]=-32768;r.sample.axes[1]=32767;r.sample.temperature=-128;r.timestampAnomaly=true;
    char line[192];int n=csvlog::format(line,sizeof(line),r);
    assert(n>0 && n<192);assert(std::string(line)=="12,4294967299,8000000000,0,-32768,32767,0,0,0,0,-128,0,1,0\n");}
  {uint8_t packet[16]={0x68,0,56,0xf8,0x0d,1,0x25,0xff,0xdd,0xff,0xe6,0xff,0xfb,0x80,0x3a,0xb2};
    vitality::Sample s;
    assert(vitality::decode(packet,s) && s.valid && !s.temperatureValid);
    assert(s.axes[1]==-2035 && s.axes[3]==-35 && s.temperature==-128);
    packet[13]=0;assert(vitality::decode(packet,s) && s.valid && s.temperatureValid);
    for(unsigned axis=0;axis<6;++axis){
      uint8_t saved[2]={packet[1+2*axis],packet[2+2*axis]};
      packet[1+2*axis]=0x80;packet[2+2*axis]=0;
      assert(vitality::decode(packet,s) && !s.valid && s.temperatureValid);
      packet[1+2*axis]=saved[0];packet[2+2*axis]=saved[1];
    }
    packet[13]=0x80;packet[1]=0x80;packet[2]=0;
    assert(vitality::decode(packet,s) && !s.valid && !s.temperatureValid);
    packet[0]=0x80;assert(!vitality::decode(packet,s));}
  {csvlog::Ring<unsigned,4> q;for(unsigned i=0;i<4;++i)assert(q.push(i));assert(!q.push(4));
    unsigned out;for(unsigned i=0;i<4;++i){assert(q.pop(out) && out==i);}assert(!q.pop(out));
    for(unsigned i=0;i<1000;++i){assert(q.push(i));assert(q.pop(out) && out==i);}}
  {csvlog::Ring<unsigned,128> q;constexpr unsigned N=200000;
    std::thread producer([&](){for(unsigned i=0;i<N;++i)while(!q.push(i))std::this_thread::yield();});
    for(unsigned i=0;i<N;++i){unsigned out;while(!q.pop(out))std::this_thread::yield();assert(out==i);}
    producer.join();assert(q.count()==0);}
  std::cout<<"CSV logger tests passed: destructive ordering, export failures, CRC, recursive erase, timestamps, CSV, concurrent queue.\n";
}
