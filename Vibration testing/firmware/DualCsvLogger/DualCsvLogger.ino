/* Teensy 4.1, TWO ICM-42688-P sensors: SEN1/J1 + SEN3/J3. Arduino USB Type = Serial.
 * Every boot: wait for USB monitor, dump /log.csv, DELETE ALL SD CONTENTS,
 * create /log.csv and record at nominal 16 kHz. No flashing is done by build.
 * STOP closes cleanly; RESET closes then reboots and dumps the recording.
 * USB export cannot prove that a terminal has displayed/saved the bytes.
 */
#include <Arduino.h>
#include <SPI.h>
#include <SdFat.h>
#include <IntervalTimer.h>
#include <stdarg.h>
#include "LoggerCore.h"

constexpr uint8_t CS[2]={10,38},INT1_PIN[2]={2,23},SENSOR_ID[2]={1,3};
constexpr uint8_t FSYNC_PIN[2]={6,21}; // Existing INT2 wires, sensor connector pin 4.
static SPIClass *const buses[2]={&SPI,&SPI1};
constexpr uint32_t SPI_HZ=8000000,QUEUE_SIZE=8192;
static uint32_t spiHz[2]={1000000,1000000};
static SdFs sd;
static FsFile logfile;
static IntervalTimer acquisition;
DMAMEM static csvlog::Ring<csvlog::Record,QUEUE_SIZE> records;
static csvlog::Ring<csvlog::SyncEvent,128> syncEvents;
static uint32_t syncId=0,lastSyncLow=0;
static uint32_t pendingSync[2]={};
static volatile uint32_t syncReceived[2]={};
static bool syncHigh=false;
DMAMEM static uint8_t writeBuffer[16384];
static size_t writeUsed=0;
static vitality::TextQueue<4096> usbTx,uartTx;
static csvlog::Clock sensorClock[2];
static volatile bool running=false;
static const char *volatile fault=nullptr;
static volatile uint32_t samples=0,invalid=0,anomalies=0,irqCount[2]={};
static volatile uint32_t sensorSamples[2]={},sensorInvalid[2]={},sensorAnomalies[2]={};
static volatile uint8_t faultSensor=0;
static volatile uint32_t tempMissing=0;
static volatile uint32_t fifoPeak=0,queuePeak=0,serviceMax=0,gapMax=0;
static uint32_t lastPoll=0,lastData[2]={},previousMicros=0;
static uint8_t nextSensor=0;
static uint64_t extendedMicros=0;
static uint64_t captureStartUs=0,lastCapturedUs=0;
static uint32_t written=0,writeMax=0,syncMax=0,syncMs=0,reportMs=0,startMs=0;
static bool fileOpen=false,sdHealthy=true,finished=false;
struct InputLine {char data[48];uint8_t used=0;bool discard=false;};
static InputLine usbInput,uartInput;

static void say(const char *fmt,...) {
  char line[320];va_list args;va_start(args,fmt);vsnprintf(line,sizeof(line),fmt,args);va_end(args);
  if(Serial)usbTx.line(line);
  uartTx.line(line);
}
static void pump() {
  if(!Serial)usbTx.clear();
  else {size_t n=min(usbTx.contiguous(),size_t(max(0,Serial.availableForWrite())));
    if(n)usbTx.consume(Serial.write(usbTx.data(),min(n,size_t(256))));}
  size_t n=min(uartTx.contiguous(),size_t(max(0,Serial1.availableForWrite())));
  if(n)uartTx.consume(Serial1.write(uartTx.data(),min(n,size_t(64))));
}
static void readRegs(unsigned sensor,uint8_t reg,uint8_t *out,size_t n) {
  SPIClass &bus=*buses[sensor];
  bus.beginTransaction(SPISettings(spiHz[sensor],MSBFIRST,SPI_MODE0));
  digitalWrite(CS[sensor],LOW);bus.transfer(reg|0x80);
  while(n--)*out++=bus.transfer(0);
  digitalWrite(CS[sensor],HIGH);bus.endTransaction();
}
static uint8_t getReg(unsigned sensor,uint8_t reg){uint8_t value;readRegs(sensor,reg,&value,1);return value;}
static void setReg(unsigned sensor,uint8_t reg,uint8_t value) {
  SPIClass &bus=*buses[sensor];
  bus.beginTransaction(SPISettings(spiHz[sensor],MSBFIRST,SPI_MODE0));
  digitalWrite(CS[sensor],LOW);bus.transfer(reg);bus.transfer(value);
  digitalWrite(CS[sensor],HIGH);bus.endTransaction();
}
static bool checked(unsigned sensor,uint8_t reg,uint8_t value) {
  setReg(sensor,reg,value);return getReg(sensor,reg)==value;
}
static void drdy1(){++irqCount[0];}
static void drdy3(){++irqCount[1];}
static void sensorFault(unsigned sensor,const char *reason){faultSensor=SENSOR_ID[sensor];fault=reason;}
// One sensor per 250 us interrupt; each sensor is serviced every 500 us.
// Separate SPI peripherals, sequential CPU service. Never SD/CSV/Serial in ISR.
static void collect() {
  if(!running || fault)return;
  unsigned sensor=nextSensor;nextSensor^=1;
  uint32_t begin=micros(),gap=begin-lastPoll;lastPoll=begin;
  if(gap>gapMax)gapMax=gap;
  extendedMicros+=uint32_t(begin-previousMicros);previousMicros=begin;
  if(syncHigh){for(auto pin:FSYNC_PIN)digitalWrite(pin,LOW);syncHigh=false;}
  if(uint32_t(begin-lastSyncLow)>=30000 && (pendingSync[0] || pendingSync[1])){
    sensorFault(pendingSync[0]?0:1,"FSYNC_TIMEOUT");return;
  }
  if(uint32_t(begin-lastSyncLow)>=10000 && !pendingSync[0] && !pendingSync[1]){
    ++syncId;lastSyncLow=begin;
    // Timestamp each wire separately. They need not rise in the same CPU cycle:
    // both edge brackets refer to the same extended Teensy micros() clock.
    for(unsigned s=0;s<2;++s){
      csvlog::SyncEvent e{};e.id=syncId;e.sensorId=SENSOR_ID[s];
      uint32_t before=micros();digitalWrite(FSYNC_PIN[s],HIGH);uint32_t after=micros();
      e.beforeUs=extendedMicros+uint32_t(before-begin);e.bracketUs=uint16_t(after-before);
      pendingSync[s]=syncId;
      if(!syncEvents.push(e)){sensorFault(s,"FSYNC_QUEUE_FULL");return;}
    }
    syncHigh=true; // Hold until the next service interrupt (~250 us).
  }
  uint8_t state=getReg(sensor,0x2d);
  if(state==0xff){sensorFault(sensor,"INVALID_STATUS");return;}
  if(state&0x02){sensorFault(sensor,"FIFO_FULL");return;}
  getReg(sensor,0x2f);uint8_t count[2];readRegs(sensor,0x2e,count,2);
  uint16_t bytes=vitality::be16(count);
  if(bytes>2080 || bytes%16){sensorFault(sensor,"BAD_FIFO_COUNT");return;}
  if(bytes>fifoPeak)fifoPeak=bytes;
  unsigned n=min(unsigned(bytes/16),24u);
  uint8_t fifo[24*16];
  if(n){
    readRegs(sensor,0x30,fifo,n*16);lastData[sensor]=begin;
    for(unsigned i=0;i<n;++i){
      csvlog::Record r{};
      if(!vitality::decode(fifo+16*i,r.sample)){sensorFault(sensor,"BAD_FIFO_HEADER");return;}
      r.sensorId=SENSOR_ID[sensor];r.sequence=sensorSamples[sensor];r.readUs=extendedMicros;
      if(r.sample.fsync){
        if(!pendingSync[sensor]){sensorFault(sensor,"UNEXPECTED_FSYNC");return;}
        r.syncId=pendingSync[sensor];pendingSync[sensor]=0;++syncReceived[sensor];
      }
      r.timestampAnomaly=sensorClock[sensor].add(r.sample.timestamp,r.sample.fsync);r.ticks=sensorClock[sensor].ticks;
      if(!records.push(r)){sensorFault(sensor,"RAM_QUEUE_FULL");return;}
      lastCapturedUs=r.readUs;
      ++samples;++sensorSamples[sensor];
      if(!r.sample.valid){++invalid;++sensorInvalid[sensor];}
      if(r.timestampAnomaly){++anomalies;++sensorAnomalies[sensor];}
      if(!r.sample.temperatureValid)++tempMissing;
    }
    uint32_t queued=records.count();if(queued>queuePeak)queuePeak=queued;
  }
  uint32_t duration=micros()-begin;if(duration>serviceMax)serviceMax=duration;
  if(uint32_t(begin-lastData[sensor])>500000)sensorFault(sensor,"NO_SAMPLES");
}
static bool configureImu(unsigned sensor) {
  pinMode(FSYNC_PIN[sensor],INPUT); // Release before reset makes pin9 INT2 again.
  pinMode(INT1_PIN[sensor],INPUT_PULLDOWN);spiHz[sensor]=1000000;setReg(sensor,0x76,0);
  for(unsigned i=0;i<100;++i){if(getReg(sensor,0x75)!=0x47){sensorFault(sensor,"WHO_AM_I");return false;}delayMicroseconds(50);}
  setReg(sensor,0x11,1);delay(5);
  if(getReg(sensor,0x75)!=0x47 || !(getReg(sensor,0x2d)&0x10)){sensorFault(sensor,"RESET_NOT_CONFIRMED");return false;}
  const uint8_t config[][2]={{0x4e,0},{0x4c,0x33},{0x16,0},
    {0x4f,2},{0x50,2},{0x51,0x16},{0x52,0x11},{0x53,0x0d},
    {0x54,0x23},{0x5f,0x0f},{0x62,0x10},{0x14,3},{0x63,0},{0x64,0x60},{0x65,8},{0x68,0}};
  for(const auto &pair:config)if(!checked(sensor,pair[0],pair[1])){sensorFault(sensor,"CONFIG_READBACK");return false;}
  setReg(sensor,0x76,1);
  bool fsyncInput=checked(sensor,0x7b,2); // PIN9_FUNCTION=01, FSYNC input.
  setReg(sensor,0x76,0);
  if(!fsyncInput){sensorFault(sensor,"FSYNC_CONFIG_READBACK");return false;}
  digitalWrite(FSYNC_PIN[sensor],LOW);pinMode(FSYNC_PIN[sensor],OUTPUT);
  setReg(sensor,0x4e,0x0f);delay(200);
  if(getReg(sensor,0x4e)!=0x0f){sensorFault(sensor,"POWER_MODE_READBACK");return false;}
  spiHz[sensor]=SPI_HZ;
  if(getReg(sensor,0x75)!=0x47){sensorFault(sensor,"IDENTITY_AT_8MHZ");return false;}
  // Leave FIFO bypassed until BOTH sensors have completed settling.
  return true;
}
static bool startImu() {
  if(!configureImu(0) || !configureImu(1))return false;
  for(unsigned sensor=0;sensor<2;++sensor){
    setReg(sensor,0x4b,2);delayMicroseconds(100);getReg(sensor,0x2d);
    if(!checked(sensor,0x16,0x80)){sensorFault(sensor,"FIFO_START");return false;}
  }
  attachInterrupt(digitalPinToInterrupt(INT1_PIN[0]),drdy1,RISING);
  attachInterrupt(digitalPinToInterrupt(INT1_PIN[1]),drdy3,RISING);
  lastPoll=lastData[0]=lastData[1]=previousMicros=micros();extendedMicros=previousMicros;
  captureStartUs=lastCapturedUs=extendedMicros;
  lastSyncLow=previousMicros;
  startMs=syncMs=reportMs=millis();running=true;
  if(!acquisition.begin(collect,250)){running=false;fault="TIMER_START";return false;}
  acquisition.priority(128);return true;
}

// Blocking USB is used ONLY before recording, to export every old CSV byte.
struct UsbSink {
  bool send(const uint8_t *data,size_t n) {
    uint32_t progress=millis();
    while(n){
      if(!Serial || uint32_t(millis()-progress)>10000)return false;
      size_t space=size_t(max(0,Serial.availableForWrite()));
      if(!space){yield();continue;}
      size_t sent=Serial.write(data,min(n,min(space,size_t(1024))));
      if(sent){data+=sent;n-=sent;progress=millis();}else yield();
    }
    return true;
  }
  bool text(const char *s){return send(reinterpret_cast<const uint8_t*>(s),strlen(s));}
  bool drain() {
    Serial.send_now();
    // Teensy core 1.62 USB Serial: four 2048-byte TX buffers; this API reports
    // the three non-head buffers. After send_now(), >=6144 means those finished.
    // This is USB transfer completion, NOT acknowledgement by the monitor app.
    uint32_t start=millis();
    while(Serial.availableForWrite()<6144){
      if(!Serial || uint32_t(millis()-start)>10000)return false;
      yield();
    }
    return bool(Serial);
  }
};
static bool waitLogAck(uint64_t size,uint32_t crc) {
  UsbSink usb;if(!usb.text("WAIT_LOG_ACK\r\n") || !usb.drain())return false;
  uint32_t start=millis();char line[96];size_t used=0;bool discard=false;
  while(Serial && uint32_t(millis()-start)<30000){
    while(Serial.available()){
      int c=Serial.read();
      if(c=='\n' || c=='\r'){
        line[used]=0;if(!discard && csvlog::ackMatches(line,size,crc))return true;
        used=0;discard=false;
      }else if(!discard){if(c<32 || c>126 || used==sizeof(line)-1)discard=true;else line[used++]=char(c);}
    }
    yield();
  }
  return false;
}
struct BootOperations {
  bool dump() {
    UsbSink usb;
    if(!sd.exists("/log.csv"))return !sd.sdErrorCode() && usb.text("NO_PREVIOUS_LOG\r\n") && usb.drain();
    FsFile previous;
    if(!previous.open("/log.csv",O_RDONLY) || previous.isDir())return false;
    uint64_t size=previous.fileSize();char marker[128];
    snprintf(marker,sizeof(marker),"\r\n=== LOG_CSV_BEGIN bytes=%llu ===\r\n",(unsigned long long)size);
    if(!usb.text(marker))return false;
    uint32_t crc;
    if(!csvlog::copyExactly(previous,usb,size,crc) || previous.getError() || !previous.close())return false;
    snprintf(marker,sizeof(marker),"\r\n=== LOG_CSV_END bytes=%llu crc32=%08lX ===\r\n",
      (unsigned long long)size,(unsigned long)crc);
    return usb.text(marker) && usb.drain() && waitLogAck(size,crc);
  }
  bool erase() {
    UsbSink usb;if(!usb.text("ERASING_ALL_SD_CONTENTS\r\n") || !usb.drain())return false;
    FsFile root;if(!root.open("/",O_RDONLY))return false;
    if(!csvlog::purge(root) || sd.sdErrorCode())return false;
    root.rewind();FsFile check;
    if(check.openNext(&root) || root.getError())return false;
    return root.close();
  }
  bool create() {
    if(!logfile.open("/log.csv",O_WRONLY|O_CREAT|O_EXCL))return false;
    fileOpen=true;
    const char *header=
      "# DualCsvLogger 2.2; sensors=1,3; nominal_odr_hz=16000; spi_hz=8000000\n"
      "# valid and invalid count refer to motion axes only; temperature_valid is independent; temp_raw=-128 is unavailable\n"
      "# accel_g=raw/2048; gyro_dps=raw*2000/32768; temp_C=raw/2.07+25\n"
      "# sync_id=0: ordinary timestamp; sync_id>0: timestamp_raw is FSYNC->ODR delta, sensor_ticks is preceding ODR counter\n"
      "# FSYNC metadata brackets each GPIO rising edge in common MCU time; interval_us=10000; pins_J1_J3=6,21\n"
      "# Independent sample clocks; app reconstructs marked sample ticks and interpolates between FSYNC anchors, without extrapolation\n"
      "# mcu_read_us is batch time, NOT sample time. No camera strobe/trigger capture in this sketch; filter delay not corrected\n"
      "# Missing END means reset/interruption; last unsynced data may be absent\n"
      "sample_index,mcu_read_us,sensor_ticks,timestamp_raw,ax_raw,ay_raw,az_raw,gx_raw,gy_raw,gz_raw,temp_raw,valid,timestamp_anomaly,temperature_valid,sensor_id,sync_id\n";
    return logfile.write(header,strlen(header))==strlen(header) && logfile.sync();
  }
};

static bool flushBuffer() {
  if(!writeUsed)return true;
  uint32_t start=micros();size_t n=logfile.write(writeBuffer,writeUsed);
  uint32_t duration=micros()-start;if(duration>writeMax)writeMax=duration;
  if(n!=writeUsed){sdHealthy=false;fault="SD_WRITE_FAILED";return false;}
  writeUsed=0;return true;
}
static bool append(const char *text,size_t n) {
  if(n>sizeof(writeBuffer)){fault="CSV_LINE_TOO_LONG";return false;}
  if(writeUsed+n>sizeof(writeBuffer) && !flushBuffer())return false;
  memcpy(writeBuffer+writeUsed,text,n);writeUsed+=n;return true;
}
static bool saveRecord(const csvlog::Record &r) {
  char line[192];int n=csvlog::format(line,sizeof(line),r);
  if(n<0 || size_t(n)>=sizeof(line)){fault="CSV_FORMAT";return false;}
  if(!append(line,size_t(n)))return false;
  ++written;return true;
}
static bool saveSyncEvents() {
  csvlog::SyncEvent e;
  while(syncEvents.pop(e)){
    char line[160];int n=snprintf(line,sizeof(line),"# FSYNC sensor=%u id=%lu before_us=%llu bracket_us=%u\n",
      unsigned(e.sensorId),(unsigned long)e.id,(unsigned long long)e.beforeUs,unsigned(e.bracketUs));
    if(n<=0 || size_t(n)>=sizeof(line) || !append(line,size_t(n)))return false;
  }
  return true;
}
static bool syncFile() {
  if(!flushBuffer())return false;
  uint32_t start=micros();bool ok=logfile.sync();
  uint32_t duration=micros()-start;if(duration>syncMax)syncMax=duration;
  if(!ok){sdHealthy=false;fault="SD_SYNC_FAILED";}
  return ok;
}
static void status() {
  say("CSV_STATUS state=%s samples=%lu csv_rows=%lu queued=%lu invalid=%lu temp_missing=%lu timestamp_anomalies=%lu fault=%s fault_sensor=%u",
    running?"RECORDING":finished?"STOPPED":"BOOT_ERROR",(unsigned long)samples,(unsigned long)written,
    (unsigned long)records.count(),(unsigned long)invalid,(unsigned long)tempMissing,(unsigned long)anomalies,fault?fault:"NONE",unsigned(faultSensor));
  say("TIMING fifo_peak_bytes=%lu queue_peak=%lu service_max_us=%lu gap_max_us=%lu sd_write_max_us=%lu sd_sync_max_us=%lu INT1_edges_J1=%lu INT1_edges_J3=%lu",
    (unsigned long)fifoPeak,(unsigned long)queuePeak,(unsigned long)serviceMax,(unsigned long)gapMax,
    (unsigned long)writeMax,(unsigned long)syncMax,(unsigned long)irqCount[0],(unsigned long)irqCount[1]);
  for(unsigned s=0;s<2;++s)say("SENSOR_STATUS sensor=%u samples=%lu invalid=%lu timestamp_anomalies=%lu",
    unsigned(SENSOR_ID[s]),(unsigned long)sensorSamples[s],(unsigned long)sensorInvalid[s],(unsigned long)sensorAnomalies[s]);
  say("SYNC_STATUS sent=%lu received_J1=%lu received_J3=%lu pending_J1=%lu pending_J3=%lu",
    (unsigned long)syncId,(unsigned long)syncReceived[0],(unsigned long)syncReceived[1],
    (unsigned long)pendingSync[0],(unsigned long)pendingSync[1]);
}
static void finish() {
  if(finished)return;
  acquisition.end();running=false;
  for(auto pin:FSYNC_PIN)digitalWrite(pin,LOW);syncHigh=false;
  for(unsigned s=0;s<2;++s){detachInterrupt(digitalPinToInterrupt(INT1_PIN[s]));
    setReg(s,0x4e,0);delayMicroseconds(250);setReg(s,0x16,0);}
  if(fileOpen){
    if(!saveSyncEvents()){sdHealthy=false;fault="FSYNC_SAVE_FAILED";}
    csvlog::Record r;
    while(sdHealthy && records.pop(r)){if(!saveRecord(r))break;pump();}
    if(sdHealthy){
      char footer[320];
      int n=snprintf(footer,sizeof(footer),"# END reason=%s samples=%lu csv_rows=%lu invalid=%lu temp_missing=%lu timestamp_anomalies=%lu elapsed_ms=%lu fifo_peak_bytes=%lu queue_peak=%lu\n",
        fault?fault:"STOP",(unsigned long)samples,(unsigned long)written,(unsigned long)invalid,
        (unsigned long)tempMissing,(unsigned long)anomalies,(unsigned long)(millis()-startMs),(unsigned long)fifoPeak,(unsigned long)queuePeak);
      if(n>0 && size_t(n)<sizeof(footer) && append(footer,size_t(n))){
        n=snprintf(footer,sizeof(footer),"# TIMING capture_us=%llu service_max_us=%lu gap_max_us=%lu sd_write_max_us=%lu sd_sync_max_us=%lu fault_sensor=%u\n",
          (unsigned long long)(lastCapturedUs-captureStartUs),(unsigned long)serviceMax,
          (unsigned long)gapMax,(unsigned long)writeMax,(unsigned long)syncMax,unsigned(faultSensor));
        if(n>0 && size_t(n)<sizeof(footer) && append(footer,size_t(n)))syncFile();
      }
    }
    if(!logfile.close()){sdHealthy=false;fault="SD_CLOSE_FAILED";}fileOpen=false;
  }
  finished=true;
  say("%s rows=%lu; DUMP downloads without erase; NEW downloads then erases ALL SD contents and records",
    sdHealthy?"FILE_CLOSED":"FILE_CLOSE_WITH_SD_ERROR",(unsigned long)written);status();
}
static void resetRunCounters() {
  syncEvents.clear();syncId=0;syncHigh=false;
  for(unsigned s=0;s<2;++s){pendingSync[s]=0;syncReceived[s]=0;}
  records.clear();samples=invalid=anomalies=tempMissing=0;
  for(unsigned s=0;s<2;++s){sensorSamples[s]=sensorInvalid[s]=sensorAnomalies[s]=irqCount[s]=0;sensorClock[s]={};}
  fault=nullptr;faultSensor=0;nextSensor=0;
  fifoPeak=queuePeak=serviceMax=gapMax=0;written=writeMax=syncMax=0;writeUsed=0;
  sdHealthy=true;finished=false;
  captureStartUs=lastCapturedUs=0;
}
static void newRecording() {
  if(running || fileOpen)finish();
  usbTx.clear(); // Do not interleave queued diagnostics with the byte-counted export.
  BootOperations ops;
  csvlog::BootResult result=csvlog::prepare(ops);
  if(result!=csvlog::BootResult::Ready){
    fault=result==csvlog::BootResult::DumpFailed?"EXPORT_OR_ACK_FAILED_NO_ERASE":
      result==csvlog::BootResult::EraseFailed?"ERASE_FAILED_MAY_BE_PARTIAL":"CREATE_FAILED";
    if(fileOpen){logfile.close();fileOpen=false;}
    say("FAIL %s; use DUMP to retry download, NEW to retry a new recording",fault);return;
  }
  resetRunCounters();
  if(!startImu()){finish();return;}
  say("RECORDING log.csv; SEN1/J1 SPI0 + SEN3/J3 SPI1; nominal 16000 samples/s EACH; DUMP or STOP to finish");
}
static void command(const char *line) {
  if(!strcmp(line,"STOP")){if(fileOpen || running)finish();else status();}
  else if(!strcmp(line,"STATUS"))status();
  else if(!strcmp(line,"DUMP")){
    if(fileOpen || running)finish();usbTx.clear();BootOperations ops;
    bool ok=ops.dump();say("%s; SD log retained",ok?"DOWNLOAD_CONFIRMED":"DOWNLOAD_FAILED");
  }else if(!strcmp(line,"NEW"))newRecording();
  else if(!strcmp(line,"RESET")){
    if(fileOpen || running)finish();
    uint32_t start=millis();while(millis()-start<100){pump();yield();}
    SCB_AIRCR=0x05FA0004;while(true){}
  }else if(!strcmp(line,"HELP"))say("DualCsvLogger 2.2: STOP, DUMP, NEW, STATUS, RESET. FSYNC on J1/J3. Downloads require ACK_LOG from Windows app. USB export only.");
}
static void input(Stream &port,InputLine &line) {
  for(unsigned budget=0;budget<64 && port.available();++budget){
    int c=port.read();
    if(c=='\r'||c=='\n'){
      if(line.used && !line.discard){line.data[line.used]=0;command(line.data);}
      line.used=0;line.discard=false;
    }else if(!line.discard){
      if(c<32||c>126||line.used==sizeof(line.data)-1){line.used=0;line.discard=true;}
      else line.data[line.used++]=char(c);
    }
  }
}
void setup() {
  Serial.begin(115200);Serial1.begin(115200);
  for(uint8_t pin:{10,9,38,37}){digitalWrite(pin,HIGH);pinMode(pin,OUTPUT);}
  for(uint8_t pin:{6,7,21,32})pinMode(pin,INPUT);
  SPI.setSCK(13);SPI.setMOSI(11);SPI.setMISO(12);SPI.begin();
  SPI1.setSCK(27);SPI1.setMOSI(26);SPI1.setMISO(39);SPI1.begin();
  // Do not erase the only copy of a recording before a USB terminal connects.
  while(!Serial)delay(10);
  delay(500);
  UsbSink usb;
  if(!usb.text("DualCsvLogger 2.2 -- J1 + J3 at 16000 samples/s EACH with FSYNC; host ACK required before ALL SD contents erased\r\n"))return;
  if(!sd.begin(SdioConfig(FIFO_SDIO))){
    fault="SD_MOUNT_FAILED";say("FAIL SD code=0x%02X data=0x%02X; no erase attempted",sd.sdErrorCode(),sd.sdErrorData());return;
  }
  newRecording();
}
void loop() {
  pump();input(Serial,usbInput);input(Serial1,uartInput);
  if(running && fault){finish();return;}
  if(running){
    if(!saveSyncEvents()){fault="FSYNC_SAVE_FAILED";finish();return;}
    csvlog::Record r;
    for(unsigned budget=0;budget<128 && records.pop(r);++budget)if(!saveRecord(r))break;
    if(fault){finish();return;}
    uint32_t now=millis();
    if(uint32_t(now-syncMs)>=1000){syncMs=now;if(!syncFile()){finish();return;}}
    if(uint32_t(now-reportMs)>=1000){reportMs=now;status();}
  }
  pump();
}
