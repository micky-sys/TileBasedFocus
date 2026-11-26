/* Standalone bring-up for Teensy 4.1 + ONE ICM-42688-P at hub SEN1/J1.
 * USB Serial Monitor and Serial1 (hub RS-232), 115200 baud, readable ASCII.
 * Not the logger/FlightBridge XP protocol. No SD writes. No LED blinking:
 * Teensy LED pin 13 is the sensor SPI clock.
 */
#include <Arduino.h>
#include <SPI.h>
#include <SdFat.h>
#include <stdarg.h>
#include <math.h>
#include "VitalityCore.h"

constexpr uint8_t CS=10, INT1_PIN=2;
constexpr uint32_t SPI_HZ=8000000, ODR_HZ=8000;
static uint32_t spiHz=1000000; // Bring-up stays slow; FIFO acquisition uses SPI_HZ.
static vitality::TextQueue<8192> usbTx,uartTx;
static vitality::TimestampCheck timestampCheck;
static uint32_t fifoPeak=0, fifoFull=0, badHeaders=0, servicePeakUs=0, serviceGapUs=0;
static uint32_t prevTimestampErrors=0;
static uint8_t fifoBytes[32*16];
static vitality::Sample latest;
static SdFs sd;
static volatile uint32_t irqCount=0;
static bool imuReady=false, sdReady=false, usbWasConnected=false;
static const char *imuFault="NOT_TESTED";
static const char *sdStage="NOT_TESTED";
static uint8_t sdCode=0,sdData=0,lastWhoBad=0;
static uint16_t whoMatches=0;
static uint32_t samples=0, invalid=0, lastSampleMs=0, reportMs=0;
static uint32_t prevSamples=0, prevInvalid=0, prevIrq=0, uartLines=0;
static uint32_t pollUs=0;
static bool haveSample=false;
struct InputLine {char data[96];uint8_t used=0;bool discard=false;};
static InputLine usbInput,uartInput;

static void say(const char *format,...) {
  char line[256];va_list args;va_start(args,format);
  vsnprintf(line,sizeof(line),format,args);va_end(args);
  if(Serial)usbTx.line(line);
  uartTx.line(line);
}
static void pumpOutput() {
  if(!Serial)usbTx.clear();
  else {size_t n=min(usbTx.contiguous(),size_t(max(0,Serial.availableForWrite())));
    if(n)usbTx.consume(Serial.write(usbTx.data(),min(n,size_t(64))));}
  size_t n=min(uartTx.contiguous(),size_t(max(0,Serial1.availableForWrite())));
  if(n)uartTx.consume(Serial1.write(uartTx.data(),min(n,size_t(64))));
}
static void readRegs(uint8_t reg,uint8_t *data,size_t n) {
  SPI.beginTransaction(SPISettings(spiHz,MSBFIRST,SPI_MODE0));
  digitalWrite(CS,LOW);SPI.transfer(reg|0x80);
  while(n--)*data++=SPI.transfer(0);
  digitalWrite(CS,HIGH);SPI.endTransaction();
}
static uint8_t getReg(uint8_t reg) {uint8_t b;readRegs(reg,&b,1);return b;}
static void setReg(uint8_t reg,uint8_t value) {
  SPI.beginTransaction(SPISettings(spiHz,MSBFIRST,SPI_MODE0));
  digitalWrite(CS,LOW);SPI.transfer(reg);SPI.transfer(value);
  digitalWrite(CS,HIGH);SPI.endTransaction();
}
static bool checkReg(uint8_t reg,uint8_t value) {
  setReg(reg,value);uint8_t actual=getReg(reg);
  if(actual==value)return true;
  say("FAIL CONFIG reg=0x%02X expected=0x%02X read=0x%02X",reg,value,actual);
  return false;
}
static void drdyISR(){++irqCount;}
static void failImu(const char *reason) {
  imuReady=false;imuFault=reason;detachInterrupt(digitalPinToInterrupt(INT1_PIN));
  say("FAIL SEN1 %s; check power/cable/orientation/soldering; RETEST after repair",reason);
}
static void pauseImu() {
  if(!imuReady)return;
  imuReady=false;detachInterrupt(digitalPinToInterrupt(INT1_PIN));
  setReg(0x4e,0);delayMicroseconds(250);setReg(0x16,0);
  imuFault="PAUSED";
  say("PAUSED: diagnostic command interrupts acquisition; RETEST restarts counters");
}
static void testSd() {
  sdReady=false;sd.end();
  say("TEST SD: Teensy built-in microSD socket, native SDIO (independent of sensor SPI)");
  if(!sd.cardBegin(SdioConfig(FIFO_SDIO))){
    sdStage="CARD_INIT_FAILED";sdCode=sd.sdErrorCode();sdData=sd.sdErrorData();
    say("FAIL SD stage=%s code=0x%02X data=0x%02X; card initialization failed BEFORE filesystem mount",
        sdStage,sdCode,sdData);return;
  }
  uint32_t sectors=sd.card()->sectorCount();
  say("PASS SD_CARD_INIT sectors=%lu capacity_MiB=%lu",(unsigned long)sectors,(unsigned long)(sectors/2048));
  if(!sd.volumeBegin()){
    sdStage="VOLUME_MOUNT_FAILED";sdCode=sd.sdErrorCode();sdData=sd.sdErrorData();
    say("FAIL SD stage=%s code=0x%02X data=0x%02X; card initialized, filesystem mount failed; do not format yet",
        sdStage,sdCode,sdData);return;
  }
  sdReady=true;sdStage="MOUNTED";sdCode=sdData=0;
  say("PASS SD_FILESYSTEM mounted; no files written, write speed NOT tested");
}
static void scanSensors() {
  const uint8_t csPins[4]={10,9,38,37};
  say("SCAN_BEGIN: J1..J4 identity only; live measurements remain SEN1/J1");
  for(uint8_t s=0;s<4;++s){
    SPIClass &bus=s<2?SPI:SPI1;uint8_t cs=csPins[s];
    for(uint32_t hz:{100000u,1000000u}){
      for(auto pin:csPins)digitalWrite(pin,HIGH);
      // Select bank 0 only. Do not reset, power up or drive FSYNC on any sensor.
      bus.beginTransaction(SPISettings(hz,MSBFIRST,SPI_MODE0));
      digitalWrite(cs,LOW);bus.transfer(0x76);bus.transfer(0);digitalWrite(cs,HIGH);
      bus.endTransaction();delayMicroseconds(10);
      uint8_t first[8]={};unsigned good=0,zero=0,ones=0,other=0;
      for(unsigned n=0;n<32;++n){
        bus.beginTransaction(SPISettings(hz,MSBFIRST,SPI_MODE0));
        digitalWrite(cs,LOW);bus.transfer(0xf5);uint8_t value=bus.transfer(0);
        digitalWrite(cs,HIGH);bus.endTransaction();
        if(n<8)first[n]=value;
        if(value==0x47)++good;else if(value==0)++zero;else if(value==0xff)++ones;else ++other;
        delayMicroseconds(50);
      }
      say("SCAN J%u SPI%u CS=%u hz=%lu match47=%u/32 zero=%u ff=%u other=%u raw=%02X:%02X:%02X:%02X:%02X:%02X:%02X:%02X",
          s+1,s<2?0:1,cs,(unsigned long)hz,good,zero,ones,other,
          first[0],first[1],first[2],first[3],first[4],first[5],first[6],first[7]);
    }
  }
  say("SCAN_END: an empty connector is expected NOT to match 0x47; raw 00/FF alone is not a diagnosis");
}
static void startImu() {
  pauseImu();spiHz=1000000;
  imuReady=false;haveSample=false;imuFault="STARTING";
  detachInterrupt(digitalPinToInterrupt(INT1_PIN));
  // Sensor pin 9 defaults to INT2. Never drive its hub FSYNC wire in this sketch.
  pinMode(6,INPUT);pinMode(INT1_PIN,INPUT_PULLDOWN);
  irqCount=0;samples=invalid=prevSamples=prevInvalid=prevIrq=0;
  fifoPeak=fifoFull=badHeaders=servicePeakUs=serviceGapUs=prevTimestampErrors=0;
  timestampCheck={};
  say("TEST SEN1/J1: SPI0 SCK=13 MOSI=11 MISO=12 CS=10 INT1=2; SPI=1MHz");
  setReg(0x76,0);
  uint16_t good=0;uint8_t firstBad=0;
  for(unsigned i=0;i<100;++i){
    uint8_t id=getReg(0x75);
    if(id==0x47)++good;else if(good==i)firstBad=id;
    delayMicroseconds(50);
  }
  say("%s WHO_AM_I expected=0x47 matches=%u/100 first_bad=0x%02X",
      good==100?"PASS":"FAIL",good,firstBad);
  whoMatches=good;lastWhoBad=firstBad;
  if(good!=100){failImu("IDENTITY_UNSTABLE_OR_MISSING");return;}
  setReg(0x11,1);delay(5); // DEVICE_CONFIG soft reset
  if(getReg(0x75)!=0x47 || !(getReg(0x2d)&0x10)){
    failImu("RESET_NOT_CONFIRMED");return;
  }
  say("PASS SOFT_RESET");
  // 8 kHz, +/-16 g and +/-2000 dps; second-order UI filters, ODR/4 bandwidth.
  // FIFO accel+gyro+temperature+ODR timestamp (16 bytes); FSYNC disabled.
  // INT1 active-high push-pull, 8 us pulses for each data-ready event.
  if(!checkReg(0x4e,0) || !checkReg(0x4c,0x33) || !checkReg(0x16,0)
     || !checkReg(0x4f,0x03) || !checkReg(0x50,0x03)
     || !checkReg(0x51,0x16) || !checkReg(0x52,0x11) || !checkReg(0x53,0x0d)
     || !checkReg(0x54,0x21) || !checkReg(0x5f,0x07) || !checkReg(0x62,0)
     || !checkReg(0x14,0x03) || !checkReg(0x63,0) || !checkReg(0x64,0x60)
     || !checkReg(0x65,0x08) || !checkReg(0x68,0)){
    failImu("CONFIG_READBACK");return;
  }
  getReg(0x2d);attachInterrupt(digitalPinToInterrupt(INT1_PIN),drdyISR,RISING);
  setReg(0x4e,0x0f);delay(200); // No register writes during power-up settling.
  if(getReg(0x4e)!=0x0f){failImu("POWER_MODE_READBACK");return;}
  spiHz=SPI_HZ;
  if(getReg(0x75)!=0x47){failImu("IDENTITY_AT_8MHZ");return;}
  setReg(0x4b,2);delayMicroseconds(100);getReg(0x2d); // FIFO flush after settling.
  if(!checkReg(0x16,0x80)){failImu("FIFO_START");return;} // stop-on-full, never silently overwrite
  irqCount=0;imuReady=true;imuFault="NONE";lastSampleMs=reportMs=millis();pollUs=micros();
  say("PASS CONFIG_READBACK; FIFO RUN odr_hz=8000 spi_hz=8000000 SEN1 only; summaries once per second");
}
static void status() {
  say("HUB_ALIVE uptime_ms=%lu SEN1=%s samples=%lu invalid=%lu INT1_edges=%lu SD=%s RS232_lines=%lu",
      (unsigned long)millis(),imuReady?"CONFIGURED":imuFault,(unsigned long)samples,
      (unsigned long)invalid,(unsigned long)irqCount,sdStage,
      (unsigned long)uartLines);
  if(!imuReady || !sdReady)say("DETAIL WHO_matches=%u/100 first_bad=0x%02X SD_stage=%s SD_code=0x%02X SD_data=0x%02X; send DIAG for full report",
      whoMatches,lastWhoBad,sdStage,sdCode,sdData);
}
static void help() {
  say("HubVitality 1.2 -- SEN1/J1 FIFO at 8000 samples/s, SPI 8MHz; identity scan: J1..J4");
  say("Commands (newline): DIAG, SCAN, PING, STATUS, RETEST, SD, HELP. USB + RS232 Serial1 115200 8N1.");
  say("Move/tilt SEN1: accel_g should change; rotate it: gyro_dps should change.");
  say("INT2/FSYNC, cameras, other sensors' measurements and supply voltage are NOT tested.");
}
static void command(char *line,bool fromUart) {
  if(fromUart)++uartLines;
  if(line[0]=='$'||line[0]=='!')return; // GPS may be present on Serial1.
  if(!strcmp(line,"PING"))say("PONG source=%s uptime_ms=%lu",fromUart?"RS232":"USB",(unsigned long)millis());
  else if(!strcmp(line,"STATUS"))status();
  else if(!strcmp(line,"RETEST")){startImu();status();}
  else if(!strcmp(line,"SD")){bool running=imuReady;pauseImu();testSd();if(running)startImu();}
  else if(!strcmp(line,"SCAN")){bool running=imuReady;pauseImu();scanSensors();if(running)startImu();}
  else if(!strcmp(line,"DIAG")){pauseImu();testSd();scanSensors();startImu();status();}
  else if(!strcmp(line,"HELP"))help();
  else say("UNKNOWN_COMMAND source=%s; use HELP (plain text, not FlightBridge XP)",fromUart?"RS232":"USB");
}
static void input(Stream &port,InputLine &line,bool fromUart) {
  for(unsigned budget=0;budget<128 && port.available();++budget){
    int c=port.read();
    if(c=='\n'||c=='\r'){
      if(line.used && !line.discard){line.data[line.used]=0;command(line.data,fromUart);}
      line.used=0;line.discard=false;
    }else if(!line.discard){
      if(c<32||c>126||line.used>=sizeof(line.data)-1){line.discard=true;line.used=0;}
      else line.data[line.used++]=char(c);
    }
  }
}
static void pollImu() {
  uint32_t begin=micros(),gap=begin-pollUs;
  if(!imuReady || gap<500)return;
  pollUs=begin;if(gap>serviceGapUs)serviceGapUs=gap;
  uint8_t state=getReg(0x2d);
  if(state==0xff){failImu("INVALID_STATUS_0xFF");return;}
  if(state&0x02){++fifoFull;failImu("FIFO_FULL");return;}
  getReg(0x2f); // Prime COUNTL latch, then read coherent COUNT H/L in byte mode.
  uint8_t count[2];readRegs(0x2e,count,2);uint16_t bytes=vitality::be16(count);
  if(bytes>2080 || bytes%16){failImu("BAD_FIFO_COUNT");return;}
  if(bytes>fifoPeak)fifoPeak=bytes;
  unsigned n=min(unsigned(bytes/16),32u);
  if(n){
    readRegs(0x30,fifoBytes,n*16);
    for(unsigned i=0;i<n;++i){
      vitality::Sample decoded;
      if(!vitality::decode(fifoBytes+i*16,decoded)){
        ++badHeaders;failImu("BAD_FIFO_HEADER");return;
      }
      ++samples;timestampCheck.add(decoded.timestamp);
      if(!decoded.valid)++invalid;
      else {latest=decoded;haveSample=true;lastSampleMs=millis();}
    }
  }
  uint32_t duration=micros()-begin;if(duration>servicePeakUs)servicePeakUs=duration;
  if(uint32_t(millis()-lastSampleMs)>500)failImu("NO_VALID_SAMPLES");
}
static void report() {
  uint32_t now=millis();if(uint32_t(now-reportMs)<1000)return;
  uint32_t interval=now-reportMs;reportMs=now;
  if(!imuReady){status();
    say("COUNTERS fifo_full=%lu bad_headers=%lu timestamp_anomalies=%lu fifo_peak_bytes=%lu",
        (unsigned long)fifoFull,(unsigned long)badHeaders,(unsigned long)timestampCheck.anomalies,(unsigned long)fifoPeak);
    return;}
  if(getReg(0x75)!=0x47){failImu("IDENTITY_LOST");return;}
  uint32_t edges=irqCount, fresh=samples-prevSamples, bad=invalid-prevInvalid;
  uint32_t newEdges=edges-prevIrq;
  float rate=fresh*1000.0f/interval;
  uint32_t timestampErrors=timestampCheck.anomalies-prevTimestampErrors;
  bool dataOk=rate>=ODR_HZ*0.95f && rate<=ODR_HZ*1.05f && bad==0 && timestampErrors==0
      && uint32_t(now-lastSampleMs)<100;
  say("%s DATA samples=%lu rate_hz=%.1f invalid=%lu interval_ms=%lu; %s INT1 edges=%lu target_hz=8000",
      dataOk?"PASS":"FAIL",(unsigned long)fresh,rate,(unsigned long)bad,(unsigned long)interval,
      newEdges?"OBSERVED":"WARN_NO_EDGES",(unsigned long)newEdges);
  say("FIFO peak_bytes=%lu full=%lu bad_headers=%lu timestamp_anomalies=%lu service_max_us=%lu gap_max_us=%lu tx_drop_usb=%lu tx_drop_uart=%lu",
      (unsigned long)fifoPeak,(unsigned long)fifoFull,(unsigned long)badHeaders,
      (unsigned long)timestampCheck.anomalies,(unsigned long)servicePeakUs,(unsigned long)serviceGapUs,
      (unsigned long)usbTx.dropped,(unsigned long)uartTx.dropped);
  prevSamples=samples;prevInvalid=invalid;prevIrq=edges;
  prevTimestampErrors=timestampCheck.anomalies;
  if(haveSample){
    float ax=latest.axes[0]/2048.0f,ay=latest.axes[1]/2048.0f,az=latest.axes[2]/2048.0f;
    say("age_ms=%lu accel_g=[%.3f %.3f %.3f] norm_g=%.3f gyro_dps=[%.2f %.2f %.2f] temp_C=%.2f",
        (unsigned long)(now-lastSampleMs),ax,ay,az,sqrtf(ax*ax+ay*ay+az*az),
        latest.axes[3]*(2000.0f/32768),latest.axes[4]*(2000.0f/32768),latest.axes[5]*(2000.0f/32768),
        latest.temperature/2.07f+25.0f);
  }
}
void setup() {
  Serial.begin(115200);Serial1.begin(115200);
  // Deselect every sensor before starting SPI; unused FSYNC wires stay inputs.
  for(uint8_t cs:{10,9,38,37}){digitalWrite(cs,HIGH);pinMode(cs,OUTPUT);}
  for(uint8_t pin:{6,7,21,32})pinMode(pin,INPUT);
  SPI.setSCK(13);SPI.setMOSI(11);SPI.setMISO(12);SPI.begin();
  SPI1.setSCK(27);SPI1.setMOSI(26);SPI1.setMISO(39);SPI1.begin();
  while(!Serial && millis()<3000)delay(10);
  delay(100);usbWasConnected=bool(Serial);help();testSd();scanSensors();startImu();status();
}
void loop() {
  pollImu();pumpOutput();
  bool connected=bool(Serial);
  if(connected && !usbWasConnected){help();status();}
  usbWasConnected=connected;
  input(Serial,usbInput,false);input(Serial1,uartInput,true);
  pollImu();report();pumpOutput();
}
