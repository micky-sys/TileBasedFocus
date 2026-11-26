#pragma once
#include <Arduino.h>
#include <SPI.h>
#include "Config.h"
#include "Core.h"

class Imu {
  SPIClass *bus=nullptr;
  uint8_t cs=0;
  uint32_t spiHz=config::SPI_HZ;
public:
  uint32_t samples=0, invalid=0, saturated=0, syncCount=0, marker=0;
  uint32_t maxFifo=0, checks=0;
  uint64_t lastSync=0;
  bool waitingSync=false,syncHigh=false;
  uint8_t aaf[7]={};
  void attach(uint8_t i) { bus=config::SENSOR[i].bus?&SPI1:&SPI; cs=config::SENSOR[i].cs; }
  void setClock(uint32_t hz) { spiHz=hz; } // Diagnostic only; never change while live.
  void read(uint8_t addr, uint8_t *out, size_t n) {
    bus->beginTransaction(SPISettings(spiHz, MSBFIRST, SPI_MODE0));
    digitalWrite(cs, LOW); bus->transfer(addr|0x80);
    while(n--)*out++=bus->transfer(0);
    digitalWrite(cs,HIGH); bus->endTransaction();
  }
  uint8_t get(uint8_t addr) { uint8_t v;read(addr,&v,1);return v; }
  void set(uint8_t addr,uint8_t v) {
    bus->beginTransaction(SPISettings(spiHz, MSBFIRST, SPI_MODE0));
    digitalWrite(cs,LOW); bus->transfer(addr); bus->transfer(v);
    digitalWrite(cs,HIGH); bus->endTransaction();
  }
  bool checked(uint8_t reg,uint8_t val) { set(reg,val);return get(reg)==val; }
  bool configure(uint8_t i) {
    pinMode(config::SENSOR[i].fsync, INPUT); // Never drive pin9 while it is INT2.
    attach(i); set(0x76,0); set(0x11,1); delay(3);
    if(get(0x75)!=0x47 || !(get(0x2d)&0x10))return false;
    if(!checked(0x4c,0x33) || !checked(0x4f,(config::GYRO_FS<<5)|config::ODR_CODE)
      || !checked(0x50,(config::ACCEL_FS<<5)|config::ODR_CODE)
      || !checked(0x51,0x16) || !checked(0x52,config::UI_BW) || !checked(0x53,0x0d)
      || !checked(0x54,0x23) || !checked(0x5f,0x2f) || !checked(0x62,0x10)
      || !checked(0x14,0x03) || !checked(0x64,0x60)
      || !checked(0x60,0x00) || !checked(0x61,0x01) // 256-byte watermark.
      || !checked(0x65,0x04) || !checked(0x68,0x00))return false;
    set(0x76,1);
    if(!checked(0x7b,0x02)){set(0x76,0);return false;}
    read(0x0b,aaf,4); // Preserve factory anti-alias and notch filters; log registers.
    set(0x76,2);read(0x03,aaf+4,3);set(0x76,0);
    digitalWrite(config::SENSOR[i].fsync,LOW);pinMode(config::SENSOR[i].fsync,OUTPUT);
    pinMode(config::SENSOR[i].int1,INPUT);
    samples=invalid=saturated=syncCount=marker=maxFifo=checks=0;
    lastSync=0;waitingSync=false;syncHigh=false;
    return true;
  }
  void power(bool on) {set(0x4e,on?0x0f:0);delayMicroseconds(250);}
  bool fifoStart() {set(0x16,0);set(0x4b,2);delayMicroseconds(100);get(0x2d);return checked(0x16,0x80);}
  uint16_t count() {
    // Reading COUNTL latches both bytes. Prime the latch before reading H then L.
    get(0x2f);uint8_t b[2];read(0x2e,b,2);return xp::be16(b);
  }
  void inspect(const uint8_t *p) {
    bool bad=false,clip=false;
    for(int j=1;j<13;j+=2) {
      int16_t v=static_cast<int16_t>(xp::be16(p+j));
      if(v==-32768)bad=true;
      if(v!=-32768 && (v>=32700 || v<=-32700))clip=true;
    }
    invalid+=bad;saturated+=clip;
    if((p[0]&0x0c)==0x0c){++syncCount;waitingSync=false;}
  }
};
