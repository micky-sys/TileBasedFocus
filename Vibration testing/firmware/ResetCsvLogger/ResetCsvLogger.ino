/* Teensy 4.1, ONE ICM-42688-P on SEN1/J1. Arduino USB Type = Serial.
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

constexpr uint8_t CS=10,INT1_PIN=2;
constexpr uint32_t SPI_HZ=8000000,QUEUE_SIZE=8192;
static uint32_t spiHz=1000000;
static SdFs sd;
static FsFile logfile;
static IntervalTimer acquisition;
DMAMEM static csvlog::Ring<csvlog::Record,QUEUE_SIZE> records;
DMAMEM static uint8_t writeBuffer[16384];
static size_t writeUsed=0;
static vitality::TextQueue<4096> usbTx,uartTx;
static csvlog::Clock sensorClock;
static volatile bool running=false;
static const char *volatile fault=nullptr;
static volatile uint32_t samples=0,invalid=0,anomalies=0,irqCount=0;
static volatile uint32_t tempMissing=0;
static volatile uint32_t fifoPeak=0,queuePeak=0,serviceMax=0,gapMax=0;
static uint32_t lastPoll=0,lastData=0,previousMicros=0;
static uint64_t extendedMicros=0;
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
static void readRegs(uint8_t reg,uint8_t *out,size_t n) {
  SPI.beginTransaction(SPISettings(spiHz,MSBFIRST,SPI_MODE0));
  digitalWrite(CS,LOW);SPI.transfer(reg|0x80);
  while(n--)*out++=SPI.transfer(0);
  digitalWrite(CS,HIGH);SPI.endTransaction();
}
static uint8_t getReg(uint8_t reg){uint8_t value;readRegs(reg,&value,1);return value;}
static void setReg(uint8_t reg,uint8_t value) {
  SPI.beginTransaction(SPISettings(spiHz,MSBFIRST,SPI_MODE0));
  digitalWrite(CS,LOW);SPI.transfer(reg);SPI.transfer(value);
  digitalWrite(CS,HIGH);SPI.endTransaction();
}
static bool checked(uint8_t reg,uint8_t value) {
  setReg(reg,value);return getReg(reg)==value;
}
static void drdyISR(){++irqCount;}
// No filesystem calls, text output or CSV conversion in this interrupt.
static void collect() {
  if(!running || fault)return;
  uint32_t begin=micros(),gap=begin-lastPoll;lastPoll=begin;
  if(gap>gapMax)gapMax=gap;
  extendedMicros+=uint32_t(begin-previousMicros);previousMicros=begin;
  uint8_t state=getReg(0x2d);
  if(state==0xff){fault="INVALID_STATUS";return;}
  if(state&0x02){fault="FIFO_FULL";return;}
  getReg(0x2f);uint8_t count[2];readRegs(0x2e,count,2);
  uint16_t bytes=vitality::be16(count);
  if(bytes>2080 || bytes%16){fault="BAD_FIFO_COUNT";return;}
  if(bytes>fifoPeak)fifoPeak=bytes;
  unsigned n=min(unsigned(bytes/16),32u);
  uint8_t fifo[32*16];
  if(n){
    readRegs(0x30,fifo,n*16);lastData=begin;
    for(unsigned i=0;i<n;++i){
      csvlog::Record r{};
      if(!vitality::decode(fifo+16*i,r.sample)){fault="BAD_FIFO_HEADER";return;}
      r.sequence=samples;r.readUs=extendedMicros;
      r.timestampAnomaly=sensorClock.add(r.sample.timestamp);r.ticks=sensorClock.ticks;
      if(!records.push(r)){fault="RAM_QUEUE_FULL";return;}
      ++samples;if(!r.sample.valid)++invalid;if(r.timestampAnomaly)++anomalies;
      if(!r.sample.temperatureValid)++tempMissing;
    }
    uint32_t queued=records.count();if(queued>queuePeak)queuePeak=queued;
  }
  uint32_t duration=micros()-begin;if(duration>serviceMax)serviceMax=duration;
  if(uint32_t(begin-lastData)>500000)fault="NO_SAMPLES";
}
static bool startImu() {
  pinMode(INT1_PIN,INPUT_PULLDOWN);spiHz=1000000;setReg(0x76,0);
  for(unsigned i=0;i<100;++i){if(getReg(0x75)!=0x47){fault="WHO_AM_I";return false;}delayMicroseconds(50);}
  setReg(0x11,1);delay(5);
  if(getReg(0x75)!=0x47 || !(getReg(0x2d)&0x10)){fault="RESET_NOT_CONFIRMED";return false;}
  // HubVitality setup with accel and gyro ODR raised to 16 kHz (code 2).
  // 16-byte FIFO packets, +/-16 g, +/-2000 dps, internal clock, no FSYNC/cameras.
  const uint8_t config[][2]={{0x4e,0},{0x4c,0x33},{0x16,0},
    {0x4f,2},{0x50,2},{0x51,0x16},{0x52,0x11},{0x53,0x0d},
    {0x54,0x21},{0x5f,7},{0x62,0},{0x14,3},{0x63,0},{0x64,0x60},{0x65,8},{0x68,0}};
  for(const auto &pair:config)if(!checked(pair[0],pair[1])){fault="CONFIG_READBACK";return false;}
  setReg(0x4e,0x0f);delay(200);
  if(getReg(0x4e)!=0x0f){fault="POWER_MODE_READBACK";return false;}
  spiHz=SPI_HZ;if(getReg(0x75)!=0x47){fault="IDENTITY_AT_8MHZ";return false;}
  setReg(0x4b,2);delayMicroseconds(100);getReg(0x2d);
  attachInterrupt(digitalPinToInterrupt(INT1_PIN),drdyISR,RISING);
  if(!checked(0x16,0x80)){fault="FIFO_START";return false;}
  lastPoll=lastData=previousMicros=micros();extendedMicros=previousMicros;
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
    return usb.text(marker) && usb.drain();
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
      "# ResetCsvLogger 1.2; SEN1/J1; nominal_odr_hz=16000; spi_hz=8000000\n"
      "# valid and invalid count refer to motion axes only; temperature_valid is independent; temp_raw=-128 is unavailable\n"
      "# accel_g=raw/2048; gyro_dps=raw*2000/32768; temp_C=raw/2.07+25\n"
      "# sensor_ticks: relative unwrapped counter; nominal tick_us=1.066666667; internal clock not calibrated\n"
      "# mcu_read_us: FIFO batch read time, NOT individual sample acquisition time\n"
      "# Missing END means reset/interruption; last unsynced data may be absent\n"
      "sample_index,mcu_read_us,sensor_ticks,timestamp_raw,ax_raw,ay_raw,az_raw,gx_raw,gy_raw,gz_raw,temp_raw,valid,timestamp_anomaly,temperature_valid\n";
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
static bool syncFile() {
  if(!flushBuffer())return false;
  uint32_t start=micros();bool ok=logfile.sync();
  uint32_t duration=micros()-start;if(duration>syncMax)syncMax=duration;
  if(!ok){sdHealthy=false;fault="SD_SYNC_FAILED";}
  return ok;
}
static void status() {
  say("CSV_STATUS state=%s samples=%lu csv_rows=%lu queued=%lu invalid=%lu temp_missing=%lu timestamp_anomalies=%lu fault=%s",
    running?"RECORDING":finished?"STOPPED":"BOOT_ERROR",(unsigned long)samples,(unsigned long)written,
    (unsigned long)records.count(),(unsigned long)invalid,(unsigned long)tempMissing,(unsigned long)anomalies,fault?fault:"NONE");
  say("TIMING fifo_peak_bytes=%lu queue_peak=%lu service_max_us=%lu gap_max_us=%lu sd_write_max_us=%lu sd_sync_max_us=%lu INT1_edges=%lu",
    (unsigned long)fifoPeak,(unsigned long)queuePeak,(unsigned long)serviceMax,(unsigned long)gapMax,
    (unsigned long)writeMax,(unsigned long)syncMax,(unsigned long)irqCount);
}
static void finish() {
  if(finished)return;
  acquisition.end();running=false;detachInterrupt(digitalPinToInterrupt(INT1_PIN));
  setReg(0x4e,0);delayMicroseconds(250);setReg(0x16,0);
  if(fileOpen){
    csvlog::Record r;
    while(sdHealthy && records.pop(r)){if(!saveRecord(r))break;pump();}
    if(sdHealthy){
      char footer[320];
      int n=snprintf(footer,sizeof(footer),"# END reason=%s samples=%lu csv_rows=%lu invalid=%lu temp_missing=%lu timestamp_anomalies=%lu elapsed_ms=%lu fifo_peak_bytes=%lu queue_peak=%lu\n",
        fault?fault:"STOP",(unsigned long)samples,(unsigned long)written,(unsigned long)invalid,
        (unsigned long)tempMissing,(unsigned long)anomalies,(unsigned long)(millis()-startMs),(unsigned long)fifoPeak,(unsigned long)queuePeak);
      if(n>0 && size_t(n)<sizeof(footer) && append(footer,size_t(n)))syncFile();
    }
    if(!logfile.close()){sdHealthy=false;fault="SD_CLOSE_FAILED";}fileOpen=false;
  }
  finished=true;
  say("%s rows=%lu; RESET exports log.csv then erases ALL SD contents and starts again",
    sdHealthy?"FILE_CLOSED":"FILE_CLOSE_WITH_SD_ERROR",(unsigned long)written);status();
}
static void command(const char *line) {
  if(!strcmp(line,"STOP"))finish();
  else if(!strcmp(line,"STATUS"))status();
  else if(!strcmp(line,"RESET")){
    if(fileOpen || running)finish();
    uint32_t start=millis();while(millis()-start<100){pump();yield();}
    SCB_AIRCR=0x05FA0004;while(true){}
  }else if(!strcmp(line,"HELP"))say("Commands: STOP, STATUS, RESET, HELP; newline; USB or Serial1 115200. Old CSV exports on USB ONLY.");
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
  // Do not erase the only copy of a recording before a USB terminal connects.
  while(!Serial)delay(10);
  delay(500);
  UsbSink usb;
  if(!usb.text("ResetCsvLogger 1.2 -- SEN1/J1 at 16000 samples/s; ALL SD contents erased after old log export\r\n"))return;
  if(!sd.begin(SdioConfig(FIFO_SDIO))){
    fault="SD_MOUNT_FAILED";say("FAIL SD code=0x%02X data=0x%02X; no erase attempted",sd.sdErrorCode(),sd.sdErrorData());return;
  }
  BootOperations ops;
  csvlog::BootResult result=csvlog::prepare(ops);
  if(result!=csvlog::BootResult::Ready){
    fault=result==csvlog::BootResult::DumpFailed?"DUMP_FAILED_NO_ERASE":
      result==csvlog::BootResult::EraseFailed?"ERASE_FAILED_MAY_BE_PARTIAL":"CREATE_FAILED";
    if(fileOpen){logfile.close();fileOpen=false;}
    say("FAIL %s; recording not started; RESET to retry",fault);return;
  }
  if(!startImu()){finish();return;}
  say("RECORDING log.csv; SEN1/J1 nominal 16000 samples/s; STOP closes, RESET closes then exports and restarts");
}
void loop() {
  pump();input(Serial,usbInput);input(Serial1,uartInput);
  if(running && fault){finish();return;}
  if(running){
    csvlog::Record r;
    for(unsigned budget=0;budget<128 && records.pop(r);++budget)if(!saveRecord(r))break;
    if(fault){finish();return;}
    uint32_t now=millis();
    if(uint32_t(now-syncMs)>=1000){syncMs=now;if(!syncFile()){finish();return;}}
    if(uint32_t(now-reportMs)>=1000){reportMs=now;status();}
  }
  pump();
}
