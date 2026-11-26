/* X-Parallax vibration logger v1.1.0, Teensy 4.1 / Teensyduino 1.62.
 * Pin mapping: hub Gerber 2026-11-03. No IMU traffic over the PC UART.
 * See ../README.md before use. Compilation is not hardware validation.
 */
#include <Arduino.h>
#include <SPI.h>
#include <SdFat.h>
#include <IntervalTimer.h>
#include "Config.h"
#include "Core.h"
#include "Imu.h"
#include "SpiTest.h"
#include "SpiTestIo.h"

using xp::Record;
DMAMEM static xp::Ring<Record,config::QUEUE_SIZE> records;
DMAMEM static uint8_t writeBuffer[16384] __attribute__((aligned(32)));
static uint8_t uartBuffer[8192];
struct CameraEdge { uint64_t time;uint8_t source,level; };
static xp::Ring<CameraEdge,256> edges;
static Record history[config::HISTORY_SIZE];
static uint32_t histHead=0,histCount=0;
static Imu imu[4];
static SpiTestIo<Imu> spiTestIo(imu,config::SPI_HZ);
static xp::SpiTest<SpiTestIo<Imu>> spiTest;
static char spiTestId[13]="";
static uint32_t spiStartMs=0,spiElapsedMs=0,spiProgressMs=0;
// Reports are paced one frame per loop, allowing GPS reception between rows.
static uint8_t spiReportNext=17;
static IntervalTimer acquisition;
static SdFs sd;
static FsFile logFile;
static std::atomic<bool> live{false};
enum Fault : uint32_t { NONE, RAM_FULL, FIFO_FULL, BAD_FIFO, NO_FSYNC, IMU_LOST,
  EVENT_FULL, SD_WRITE, SD_OPEN, UART_OVERRUN, UART_LINE, STROBE_STUCK, BURST_LIMIT,
  ACQ_LATE, TIMER_FAILED, SPI_RESTORE };
static const char *faultNames[]={"NONE","RAM_FULL","FIFO_FULL","BAD_FIFO","NO_FSYNC",
  "IMU_LOST","EVENT_FULL","SD_WRITE","SD_OPEN","UART_OVERRUN","UART_LINE",
  "STROBE_STUCK","BURST_LIMIT","ACQ_LATE","TIMER_FAILED","SPI_RESTORE"};
static std::atomic<uint32_t> fault{NONE};
enum Mode { IDLE, ARMED, CONTINUOUS, ERROR_STATE, TESTING };
static Mode mode=IDLE;
static xp::Window window;
static bool fileReady=false,sdReady=false,storageFailed=false;
static uint32_t sequence=0,session=0,segment=0,writeUsed=0;
static uint64_t fileBytes=0,lastSyncFile=0,started=0;
static uint32_t queuePeak=0,servicePeak=0,writePeak=0,captures=0,nmeaCount=0;
static uint32_t completedPending=0;
static uint8_t nextSensor=0;
static uint64_t serviceLast[4]={},strobeRise[3]={};
static bool strobeHigh[3]={};

// Extend micros() across wrap; interrupts disabled only for this short clock read.
static uint64_t clockUs() {
  uint32_t mask;__asm__ volatile("mrs %0, primask" : "=r" (mask));__disable_irq();
  static uint32_t prev=0,hi=0;
  uint32_t t=micros();if(t<prev)++hi;prev=t;
  uint64_t r=(uint64_t(hi)<<32)|t;
  if(!mask)__enable_irq();return r;
}
static void fail(Fault f) {uint32_t expected=NONE;fault.compare_exchange_strong(expected,f);}
static bool enqueue(const Record &r) {
  if(!records.push(r)){fail(RAM_FULL);return false;}
  uint32_t n=records.count();if(n>queuePeak)queuePeak=n;return true;
}
static void cameraEdge(uint8_t source,uint8_t pin) {
  if(!live.load(std::memory_order_relaxed))return;
  CameraEdge e{clockUs(),source,uint8_t(digitalReadFast(pin))};
  if(!edges.push(e))fail(EVENT_FULL);
}
static void triggerISR(){cameraEdge(0,config::TRIGGER);}
static void strobe1ISR(){cameraEdge(1,config::STROBE[0]);}
static void strobe2ISR(){cameraEdge(2,config::STROBE[1]);}
static void strobe3ISR(){cameraEdge(3,config::STROBE[2]);}

// Only the timer owns SPI while live. SD, formatting and UART never run here.
static void collect(uint8_t s,bool syncEnabled) {
  Imu &m=imu[s];uint64_t t=clockUs();
  // Hold each FSYNC pulse until the next service (~1 ms), without busy waiting.
  if(m.syncHigh){digitalWrite(config::SENSOR[s].fsync,LOW);m.syncHigh=false;}
  uint8_t status=m.get(0x2d);
  if(status&2){fail(FIFO_FULL);return;}
  uint16_t bytes=m.count();if(bytes>m.maxFifo)m.maxFifo=bytes;
  if(bytes>2048){fail(BAD_FIFO);return;}
  uint8_t n=min(uint16_t(config::PACKETS_PER_READ),uint16_t(bytes/16));
  if(n) {
    Record r{};r.time=t;r.type=xp::FIFO;r.source=s;r.size=8+16*n;
    xp::put32(r.data,m.samples);xp::put32(r.data+4,n);
    m.read(0x30,r.data+8,n*16);
    for(uint8_t j=0;j<n;++j) {
      uint8_t *p=r.data+8+j*16;
      if((p[0]&0xf0)!=0x60 || (p[0]&0x08)==0){fail(BAD_FIFO);return;}
      m.inspect(p);
    }
    m.samples+=n;if(!enqueue(r))return;
  }
  if(!syncEnabled)return;
  if(++m.checks%1000==0 && m.get(0x75)!=0x47){fail(IMU_LOST);return;}
  if(m.waitingSync && t-m.lastSync>30000){fail(NO_FSYNC);return;}
  if((!m.lastSync || t-m.lastSync>=config::FSYNC_US) && !m.waitingSync) {
    // Timestamp before and after the GPIO write bounds the edge's software uncertainty.
    Record r{};r.type=xp::SYNC;r.source=s;r.size=12;r.time=clockUs();
    digitalWrite(config::SENSOR[s].fsync,HIGH);
    uint32_t uncertainty=uint32_t(clockUs()-r.time);
    m.syncHigh=true;
    xp::put32(r.data,++m.marker);xp::put32(r.data+4,uncertainty);
    xp::put32(r.data+8,m.samples);
    m.lastSync=r.time;m.waitingSync=true;enqueue(r);
  }
}
static void acquisitionISR() {
  if(!live.load(std::memory_order_relaxed)||fault.load()!=NONE)return;
  uint32_t begin=micros();uint8_t s=nextSensor;nextSensor=(s+1)%4;
  uint64_t now=clockUs();
  if(serviceLast[s] && now-serviceLast[s]>6000){fail(ACQ_LATE);return;}
  serviceLast[s]=now;collect(s,true);
  uint32_t duration=micros()-begin;if(duration>servicePeak)servicePeak=duration;
  if(duration>=config::SERVICE_US)fail(ACQ_LATE);
}

static const char *stateName() {
  if(fault.load()!=NONE || mode==ERROR_STATE)return "ERROR";
  if(mode==ARMED)return "ARMED";
  if(mode==TESTING)return "TESTING";
  if(mode==CONTINUOUS)return "RECORDING";return "IDLE";
}
static void reply(const char *id,const char *result,const char *state,const char *detail) {
  char p[220],frame[232];snprintf(p,sizeof(p),"XP,1,R,%s,%s,%s,%s",id,result,state,detail);
  uint8_t sum=0;for(char *q=p;*q;++q)sum^=uint8_t(*q);
  snprintf(frame,sizeof(frame),"@%s*%02X\r\n",p,sum);Serial1.print(frame);
  if(Serial && Serial.availableForWrite()>=int(strlen(frame)))Serial.print(frame);
}
static void spiTestService() {
  if(mode==TESTING){
    uint32_t begin=micros();
    // Reset/configuration takes ~3 ms; pattern steps are short. Yield to Serial1
    // between slices, including STOP, STATUS and incoming GPS sentences.
    do {spiTest.step(spiTestIo);} while(spiTest.active() && uint32_t(micros()-begin)<1000);
    if(spiTest.done()){
      spiElapsedMs=millis()-spiStartMs;
      if(spiTest.restoreFailed){fail(SPI_RESTORE);mode=ERROR_STATE;}
      else mode=IDLE;
      spiReportNext=0;
    }else if(uint32_t(millis()-spiProgressMs)>=1000){
      spiProgressMs=millis();char b[180];
      snprintf(b,sizeof(b),"SPI_PROGRESS;test=%s;mhz=%lu;round=%lu;target=%lu;errors=%u",
        spiTestId,(unsigned long)(spiTest.rates[spiTest.rate]/1000000),
        (unsigned long)spiTest.round,(unsigned long)spiTest.target,unsigned(spiTest.failed()));
      reply("0","OK",stateName(),b);
    }
  }
  if(spiReportNext>=17 || live.load())return;
  char b[190];const char *result="OK";
  if(spiReportNext<16){
    uint8_t rate=spiReportNext/4,s=spiReportNext%4;auto &r=spiTest.results[rate][s];
    const char *status=r.accessErrors||r.badBytes?"FAIL":
      r.finished && r.rounds==spiTest.target?"PASS":r.started?"PARTIAL":"NOT_RUN";
    if(r.accessErrors||r.badBytes)result="ERR";
    snprintf(b,sizeof(b),"SPI_ROW;test=%s;mhz=%lu;s=%u;n=%lu;bad_bytes=%lu;bad_bits=%lu;access=%lu;result=%s;first=%lu:%u:%02X:%02X",
      spiTestId,(unsigned long)(spiTest.rates[rate]/1000000),s+1,(unsigned long)r.rounds,
      (unsigned long)r.badBytes,(unsigned long)r.badBits,(unsigned long)r.accessErrors,status,
      (unsigned long)r.firstRound,r.firstByte,r.expected,r.actual);
  }else{
    const char *status=spiTest.active()?"RUNNING":spiTest.restoreFailed?"RESTORE_FAILED":
      spiTest.cancelled?"CANCELLED":spiTest.passed()?"PASS":"FAIL";
    if(!spiTest.active() && !spiTest.passed())result="ERR";
    snprintf(b,sizeof(b),"SPI_SUMMARY;test=%s;result=%s;restore_mask=%u;elapsed_ms=%lu;target=%lu",
      spiTestId,status,spiTest.restoreFailed,
      (unsigned long)(spiTest.active()?millis()-spiStartMs:spiElapsedMs),(unsigned long)spiTest.target);
  }
  ++spiReportNext;reply("0",result,stateName(),b);
}
static bool flushBytes() {
  if(storageFailed)return false;
  if(!writeUsed)return true;
  uint32_t t=micros();size_t n=logFile.write(writeBuffer,writeUsed);
  uint32_t dt=micros()-t;if(dt>writePeak)writePeak=dt;
  if(n!=writeUsed){storageFailed=true;fail(SD_WRITE);return false;}
  fileBytes+=writeUsed;writeUsed=0;return true;
}
static bool bytesOut(const void *p,size_t n) {
  const uint8_t *b=static_cast<const uint8_t*>(p);
  while(n){size_t k=min(n,sizeof(writeBuffer)-writeUsed);memcpy(writeBuffer+writeUsed,b,k);
    writeUsed+=k;b+=k;n-=k;if(writeUsed==sizeof(writeBuffer)&&!flushBytes())return false;}
  return true;
}
static bool output(const Record &r) {
  if(!fileReady || storageFailed || r.size>xp::MAX_PAYLOAD)return false;
  xp::Header h{xp::MAGIC,r.size,r.type,r.source,sequence++,r.time,0};
  h.crc=xp::recordCRC(h,r.data);return bytesOut(&h,sizeof(h))&&bytesOut(r.data,r.size);
}
static void textRecord(uint8_t type,uint8_t source,uint64_t t,const char *text) {
  Record r{};r.type=type;r.source=source;r.time=t;r.size=min(strlen(text),xp::MAX_PAYLOAD);
  memcpy(r.data,text,r.size);output(r);
}
static bool newSegment() {
  char name[32];snprintf(name,sizeof(name),"XP%05lu_%03lu.XPL",(unsigned long)session,(unsigned long)segment);
  if(!logFile.open(name,O_WRONLY|O_CREAT|O_EXCL)){fail(SD_OPEN);return false;}
  fileReady=true;fileBytes=0;writeUsed=0;
  char meta[400];snprintf(meta,sizeof(meta),
    "{\"firmware\":\"1.1.0\",\"format\":1,\"session\":%lu,\"segment\":%lu,\"mode\":\"%s\",\"odr_hz\":%lu,\"gyro_dps\":2000,\"accel_g\":16,\"spi_hz\":%lu,\"pre_us\":%lu,\"post_us\":%lu,\"fsync_period_us\":%lu,\"fifo_tick_us\":1.066666666667,\"time\":\"mcu_boot_us\"}",
    (unsigned long)session,(unsigned long)segment,mode==ARMED?"triggered":"continuous",
    (unsigned long)config::ODR_HZ,(unsigned long)config::SPI_HZ,(unsigned long)config::PRE_US,
    (unsigned long)config::POST_US,(unsigned long)config::FSYNC_US);
  textRecord(xp::META,255,clockUs(),meta);
  snprintf(meta,sizeof(meta),"{\"uart\":\"Serial1\",\"baud\":115200,\"trigger_pin\":14,\"trigger_active_high\":%u,\"strobe_pins\":[35,34,33],\"strobe_enabled\":[%u,%u,%u],\"strobe_active_high\":[%u,%u,%u],\"spi0\":[13,11,12],\"spi1\":[27,26,39]}",
    config::TRIGGER_ACTIVE_HIGH,config::STROBE_ENABLED[0],config::STROBE_ENABLED[1],config::STROBE_ENABLED[2],
    config::STROBE_ACTIVE_HIGH[0],config::STROBE_ACTIVE_HIGH[1],config::STROBE_ACTIVE_HIGH[2]);
  textRecord(xp::META,254,clockUs(),meta);
  for(uint8_t s=0;s<4;++s){
    auto &p=config::SENSOR[s];auto &m=imu[s];
    snprintf(meta,sizeof(meta),"{\"sensor\":%u,\"cs\":%u,\"int1\":%u,\"fsync\":%u,\"bus\":%u,\"gyro_config0\":2,\"accel_config0\":2,\"gyro_config1\":22,\"accel_config1\":13,\"ui_bw\":17,\"tmst\":35,\"aaf\":[%u,%u,%u,%u,%u,%u,%u]}",s+1,p.cs,p.int1,p.fsync,p.bus,m.aaf[0],m.aaf[1],m.aaf[2],m.aaf[3],m.aaf[4],m.aaf[5],m.aaf[6]);
    textRecord(xp::META,s,clockUs(),meta);
  }
  if(Serial && Serial.availableForWrite()>=64){Serial.print("Logging ");Serial.println(name);}
  return fault.load()==NONE;
}
static void stats() {
  char b[400];snprintf(b,sizeof(b),"{\"queue_peak\":%lu,\"service_max_us\":%lu,\"write_max_us\":%lu,\"captures\":%lu,\"merged_triggers\":%lu,\"nmea\":%lu,\"fault\":\"%s\"}",
    (unsigned long)queuePeak,(unsigned long)servicePeak,(unsigned long)writePeak,(unsigned long)captures,
    (unsigned long)window.merged,(unsigned long)nmeaCount,faultNames[fault.load()]);
  textRecord(xp::STATS,255,clockUs(),b);
  for(uint8_t s=0;s<4;++s){auto &m=imu[s];snprintf(b,sizeof(b),"{\"samples_read\":%lu,\"invalid\":%lu,\"saturated\":%lu,\"fsync\":%lu,\"max_fifo_bytes\":%lu}",
    (unsigned long)m.samples,(unsigned long)m.invalid,(unsigned long)m.saturated,(unsigned long)m.syncCount,(unsigned long)m.maxFifo);
    textRecord(xp::STATS,s,clockUs(),b);}
}
static void remember(const Record &r) {
  history[histHead]=r;histHead=(histHead+1)%config::HISTORY_SIZE;
  if(histCount<config::HISTORY_SIZE)++histCount;
}
static void beginCapture(uint64_t t) {
  bool fresh=!window.active;window.trigger(t,config::PRE_US,config::POST_US);
  if(window.end-window.first>config::MAX_BURST_US){fail(BURST_LIMIT);return;}
  if(fresh){
    ++captures;char b[160];snprintf(b,sizeof(b),"{\"capture\":%lu,\"event\":\"begin\",\"requested_start_us\":%llu,\"warmup\":%s}",
      (unsigned long)captures,(unsigned long long)window.begin,t-started<config::PRE_US?"true":"false");
    textRecord(xp::BURST,255,t,b);
    uint32_t first=(histHead+config::HISTORY_SIZE-histCount)%config::HISTORY_SIZE;
    for(uint32_t j=0;j<histCount;++j){auto &r=history[(first+j)%config::HISTORY_SIZE];
      // Batch read time can trail samples. Include a 2 ms boundary margin.
      if(r.time+2000>=window.begin)output(r);}
  }else textRecord(xp::BURST,255,t,"{\"event\":\"merged_trigger\"}");
}
static void processEdges() {
  CameraEdge e;
  while(edges.pop(e)){
    if(e.source==0 && bool(e.level)==config::TRIGGER_ACTIVE_HIGH && mode==ARMED)beginCapture(e.time);
    if(e.source && config::STROBE_ENABLED[e.source-1]){uint8_t s=e.source-1;
      strobeHigh[s]=bool(e.level)==config::STROBE_ACTIVE_HIGH[s];
      if(strobeHigh[s])strobeRise[s]=e.time;
      if(mode==ARMED && window.active)window.extend(e.time,config::POST_US);}
    Record r{};r.type=xp::EDGE;r.source=e.source;r.time=e.time;r.size=1;r.data[0]=e.level;output(r);
  }
}
static void processRecord(const Record &r) {
  if(mode==CONTINUOUS){output(r);return;}
  bool high=strobeHigh[0]||strobeHigh[1]||strobeHigh[2];
  if(window.active && r.time>window.end+2000 && !high){
    textRecord(xp::BURST,255,window.end,"{\"event\":\"end\"}");window.active=false;++completedPending;
    // Completion event is issued only after a subsequent successful SD sync.
  }
  if(window.active && r.time+2000>=window.begin)output(r);
  remember(r);
}
static bool prepareSensors() {
  for(uint8_t s=0;s<4;++s)if(!imu[s].configure(s)){fail(IMU_LOST);return false;}
  for(auto &m:imu)m.power(true);
  delay(200); // Gyro startup and digital-filter settling precede accepted samples.
  return true;
}
static bool startLogger(Mode requested) {
  if(mode!=IDLE)return false;
  fault.store(NONE);storageFailed=false;records.clear();edges.clear();window={};histHead=histCount=0;
  sequence=segment=writeUsed=queuePeak=servicePeak=writePeak=captures=nmeaCount=completedPending=0;
  memset(serviceLast,0,sizeof(serviceLast));memset(strobeRise,0,sizeof(strobeRise));
  nextSensor=0;sdReady=sd.begin(SdioConfig(FIFO_SDIO));
  if(!sdReady){fail(SD_OPEN);mode=ERROR_STATE;return false;}
  if(!prepareSensors()){mode=ERROR_STATE;return false;}
  char name[32];for(session=1;session<=99999;++session){
    snprintf(name,sizeof(name),"XP%05lu_000.XPL",(unsigned long)session);if(!sd.exists(name))break;}
  if(session>99999){fail(SD_OPEN);mode=ERROR_STATE;return false;}
  mode=requested;
  if(!newSegment() || !flushBytes() || !logFile.sync()){fail(SD_WRITE);return false;}
  for(uint8_t s=0;s<4;++s){if(!imu[s].fifoStart()){fail(IMU_LOST);return false;}}
  started=lastSyncFile=clockUs();
  for(uint8_t s=0;s<3;++s){strobeHigh[s]=config::STROBE_ENABLED[s] &&
      bool(digitalRead(config::STROBE[s]))==config::STROBE_ACTIVE_HIGH[s];strobeRise[s]=started;}
  // Record initial input levels, including a high strobe whose rising edge predates ARM.
  for(uint8_t s=0;s<4;++s){Record r{};r.type=xp::EDGE;r.source=s;r.time=started;r.size=2;
    r.data[0]=digitalRead(s?config::STROBE[s-1]:config::TRIGGER);r.data[1]=1;output(r);}
  live.store(true);
  acquisition.priority(128);
  if(!acquisition.begin(acquisitionISR,config::SERVICE_US)){live.store(false);fail(TIMER_FAILED);return false;}
  return true;
}
static void stopLogger(bool faulted) {
  live.store(false);acquisition.end();
  for(uint8_t s=0;s<4;++s)if(imu[s].syncHigh){digitalWrite(config::SENSOR[s].fsync,LOW);imu[s].syncHigh=false;}
  if(fileReady){
    // Stop production before the final drain. The FIFO retains previously acquired data.
    for(auto &m:imu)m.power(false);
    if(!faulted){for(uint8_t s=0;s<4;++s)for(uint8_t j=0;j<6 && imu[s].count()>=16;++j)collect(s,false);}
    processEdges();Record r;while(records.pop(r))processRecord(r);
    if(window.active){textRecord(xp::BURST,255,clockUs(),"{\"event\":\"stop_truncated\"}");window.active=false;}
    stats();textRecord(xp::END,255,clockUs(),fault.load()==NONE?"{\"clean\":true}":"{\"clean\":false}");
    if(!flushBytes() || !logFile.sync())fail(SD_WRITE);
    if(!logFile.close())fail(SD_WRITE);fileReady=false;
  }else for(auto &m:imu)m.power(false);
  mode=fault.load()==NONE?IDLE:ERROR_STATE;
}
static const char *health() {
  if(fault.load()!=NONE)return faultNames[fault.load()];
  if(live.load())return sdReady && fileReady && !sd.sdErrorCode()?"HEALTHY":"SD_ERROR";
  sdReady=sd.begin(SdioConfig(FIFO_SDIO));if(!sdReady)return "SD_MISSING";
  static const char *bad[4]={"IMU1_NO_RESPONSE","IMU2_NO_RESPONSE","IMU3_NO_RESPONSE","IMU4_NO_RESPONSE"};
  for(uint8_t s=0;s<4;++s){imu[s].set(0x76,0);if(imu[s].get(0x75)!=0x47)return bad[s];}
  return "HEALTHY";
}
struct Cached {char id[13],result[4],state[10],detail[40];};
static Cached cache[8]={};static uint8_t cacheNext=0;
static void control(char *line) {
  char *id,*verb;if(!xp::command(line,id,verb))return;
  for(auto &c:cache)if(!strcmp(c.id,id)){reply(c.id,c.result,c.state,c.detail);return;}
  bool ok=true;const char *detail="READY";
  if(!strcmp(verb,"START")||!strcmp(verb,"ARM")){
    Mode wanted=!strcmp(verb,"ARM")?ARMED:CONTINUOUS;
    if(mode==wanted && fault.load()==NONE)detail=wanted==ARMED?"ARMED":"STARTED";
    else if(mode!=IDLE){ok=false;detail="INVALID_STATE";}
    else {ok=startLogger(wanted);detail=ok?(wanted==ARMED?"ARMED":"STARTED"):faultNames[fault.load()];
      if(!ok)stopLogger(true);}
  }else if(!strcmp(verb,"STOP")){
    if(mode==TESTING){spiTest.cancel();detail="SPI_TEST_STOPPING";}
    else {if(live.load()||fileReady)stopLogger(false);
      ok=fault.load()==NONE;detail=ok?"STOPPED":faultNames[fault.load()];}
  }else if(!strcmp(verb,"SPITEST")){
    if(mode!=IDLE || fault.load()!=NONE){ok=false;detail="INVALID_STATE";}
    else {spiTest.start();mode=TESTING;spiReportNext=17;
      snprintf(spiTestId,sizeof(spiTestId),"%s",id);
      spiStartMs=spiProgressMs=millis();spiElapsedMs=0;detail="SPI_TEST_STARTED";}
  }else if(!strcmp(verb,"SPITESTRESULT")){
    if(live.load()){ok=false;detail="INVALID_STATE";}
    else if(!spiTest.available()){ok=false;detail="NO_SPI_RESULTS";}
    else {spiReportNext=0;detail="SPI_RESULTS";}
  }else if(!strcmp(verb,"HEALTH")){
    if(mode==TESTING){ok=false;detail="SPI_TEST_BUSY";}
    else {detail=health();ok=!strcmp(detail,"HEALTHY");}
  }else if(!strcmp(verb,"STATUS")){ok=fault.load()==NONE;
    detail=ok?(mode==TESTING?"SPI_TEST_RUNNING":"READY"):faultNames[fault.load()];}
  else{ok=false;detail="UNKNOWN_COMMAND";}
  auto &c=cache[cacheNext++%8];snprintf(c.id,sizeof(c.id),"%s",id);
  snprintf(c.result,sizeof(c.result),"%s",ok?"OK":"ERR");snprintf(c.state,sizeof(c.state),"%s",stateName());
  snprintf(c.detail,sizeof(c.detail),"%s",detail);reply(c.id,c.result,c.state,c.detail);
}
static void onNmea(const char *line) {
  ++nmeaCount;uint64_t lastGpsTime=clockUs();
  if(!fileReady)return;
  // Up to 1024 bytes: several GPS records share timestamp and fragment index.
  size_t n=strlen(line),offset=0;uint8_t part=0;
  while(offset<n){Record r{};r.time=lastGpsTime;r.type=xp::GPS;r.source=part++;
    r.size=min(n-offset,xp::MAX_PAYLOAD);memcpy(r.data,line+offset,r.size);output(r);offset+=r.size;}
}
static void serialPoll() {
  static char line[1025];static uint16_t used=0;static bool discard=false;
  if(live.load() && Serial1.available()>7000)fail(UART_OVERRUN);
  // Bound parsing work so sustained input cannot starve file draining.
  for(int budget=0;budget<512 && Serial1.available();++budget){int v=Serial1.read();
    if(v=='\r'||v=='\n'){
      if(used&&!discard){line[used]=0;if(line[0]=='$'||line[0]=='!')onNmea(line);else control(line);}
      used=0;discard=false;
    }else if(!discard){
      if(v<32||v>126||used>=1024){discard=true;used=0;if(live.load())fail(UART_LINE);}
      else line[used++]=char(v);
    }
  }
  // Teensy HardwareSerial drops bytes when its ring is full. Stop before that point.
}
void setup() {
  Serial.begin(115200);Serial1.addMemoryForRead(uartBuffer,sizeof(uartBuffer));Serial1.begin(config::UART_BAUD);
  for(uint8_t s=0;s<4;++s){pinMode(config::SENSOR[s].cs,OUTPUT);digitalWrite(config::SENSOR[s].cs,HIGH);imu[s].attach(s);}
  SPI.begin();SPI1.setMISO(39);SPI1.setMOSI(26);SPI1.setSCK(27);SPI1.begin();
  pinMode(config::TRIGGER,INPUT_PULLDOWN);for(auto pin:config::STROBE)pinMode(pin,INPUT_PULLDOWN);
  attachInterrupt(digitalPinToInterrupt(config::TRIGGER),triggerISR,CHANGE);
  attachInterrupt(digitalPinToInterrupt(config::STROBE[0]),strobe1ISR,CHANGE);
  attachInterrupt(digitalPinToInterrupt(config::STROBE[1]),strobe2ISR,CHANGE);
  attachInterrupt(digitalPinToInterrupt(config::STROBE[2]),strobe3ISR,CHANGE);
  NVIC_SET_PRIORITY(IRQ_GPIO6789,32); // GPIO timing preempts UART (64) and acquisition (128).
  reply("0","OK","IDLE","BOOT");
}
void loop() {
  clockUs();serialPoll();
  spiTestService();
  if(live.load()){
    processEdges();Record r;
    for(int i=0;i<32 && records.pop(r);++i){processEdges();processRecord(r);}
    uint64_t now=clockUs();
    for(uint8_t s=0;s<3;++s)if(strobeHigh[s] && now-strobeRise[s]>config::STROBE_TIMEOUT_US)fail(STROBE_STUCK);
    if(window.active && now-window.first>config::MAX_BURST_US)fail(BURST_LIMIT);
    if(now-lastSyncFile>=1000000 && fault.load()==NONE){
      stats();if(!flushBytes() || !logFile.sync())fail(SD_WRITE);lastSyncFile=now;
      if(completedPending && fault.load()==NONE){reply("0","OK",stateName(),"CAPTURE_DONE");completedPending=0;}
    }
    if(fileBytes+writeUsed>=config::SEGMENT_BYTES && fault.load()==NONE){
      textRecord(xp::END,255,now,"{\"continuation\":true}");
      if(!flushBytes()||!logFile.sync()||!logFile.close())fail(SD_WRITE);
      fileReady=false;if(fault.load()==NONE){++segment;newSegment();}
    }
    if(fault.load()!=NONE){stopLogger(true);reply("0","ERR","ERROR",faultNames[fault.load()]);}
  }
}
