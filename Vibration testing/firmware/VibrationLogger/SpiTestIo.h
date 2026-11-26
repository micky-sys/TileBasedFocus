#pragma once
#include <stdint.h>

// ICM-42688-P DS-000347 rev 1.6: sections 5.4, 12.9, 17.18-17.26.
// ONLY bank 4 OFFSET_USER0..8 (0x77..0x7f) receive test patterns. Accel/gyro
// stay OFF. These are volatile offset registers, not calibration OTP or FIFO RAM.
// A full reset/reconfigure removes diagnostic state, including possible bad writes.
template<class Device> class SpiTestIo {
  Device (&devices)[4];
  uint32_t normalHz;
  static constexpr uint32_t safeHz=1000000;
  bool bank4(uint8_t s,uint32_t hz){
    auto &m=devices[s];m.setClock(safeHz);
    // Check before *every* access. Never continue pattern writes in an unknown bank.
    if(m.get(0x76)!=4)return false;
    m.setClock(hz);return true;
  }
public:
  SpiTestIo(Device (&d)[4],uint32_t hz):devices(d),normalHz(hz){}
  bool prepare(uint8_t s){
    auto &m=devices[s];m.setClock(safeHz);
    if(!m.configure(s))return false; // Soft reset, normal config, FSYNC wire tri-stated during reset.
    if(!m.checked(0x4e,0) || !m.checked(0x16,0))return false;
    return m.checked(0x76,4);
  }
  bool write(uint8_t s,uint32_t hz,const uint8_t *p){
    // Individual writes exercise address/CS transitions; readback is a 9-byte burst.
    // Check the bank before each write in case the prior corrupted address hit BANK_SEL.
    for(uint8_t j=0;j<9;++j){if(!bank4(s,hz))return false;devices[s].set(0x77+j,p[j]);}
    return true;
  }
  bool read(uint8_t s,uint32_t hz,uint8_t *p){
    if(!bank4(s,hz))return false;
    devices[s].read(0x77,p,9);return true;
  }
  bool restore(uint8_t s){
    auto &m=devices[s];m.setClock(safeHz);
    bool ok=m.configure(s);
    if(ok)ok=m.checked(0x4e,0) && m.checked(0x16,0) && m.checked(0x76,4);
    if(ok){uint8_t zero[9];m.read(0x77,zero,9);for(auto b:zero)if(b)ok=false;}
    // Attempt bank recovery even after a failed verification.
    bool bankOk=m.checked(0x76,0);
    m.setClock(normalHz);return ok && bankOk;
  }
};
