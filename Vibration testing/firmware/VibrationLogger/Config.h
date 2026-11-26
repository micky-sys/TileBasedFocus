#pragma once
#include <stdint.h>

namespace config {
constexpr uint32_t UART_BAUD = 115200;
constexpr uint32_t SPI_HZ = 16000000;
constexpr uint32_t ODR_HZ = 16000;
constexpr uint8_t ODR_CODE = 2;  // ICM-42688-P: 16 kHz, both sensors in LN mode.
constexpr uint8_t GYRO_FS = 0;   // +/-2000 degrees/second.
constexpr uint8_t ACCEL_FS = 0;  // +/-16 g.
constexpr uint8_t UI_BW = 0x11; // Second-order UI filters, ODR/4 = 4 kHz.
constexpr uint32_t SERVICE_US = 250; // One sensor per interrupt, round robin.
constexpr uint32_t FSYNC_US = 10000;
constexpr uint32_t PRE_US = 20000;
constexpr uint32_t POST_US = 20000;
constexpr uint32_t STROBE_TIMEOUT_US = 250000;
constexpr uint32_t MAX_BURST_US = 1000000;
constexpr uint64_t SEGMENT_BYTES = 512ull * 1024 * 1024;
constexpr uint8_t TRIGGER = 14;
constexpr uint8_t STROBE[3] = {35, 34, 33};
constexpr bool TRIGGER_ACTIVE_HIGH = true;
constexpr bool STROBE_ENABLED[3] = {true, true, true};
constexpr bool STROBE_ACTIVE_HIGH[3] = {true, true, true};
// These are Teensy GPIO numbers, NOT connector pad numbers.
struct Pins { uint8_t bus, cs, int1, fsync; };
constexpr Pins SENSOR[4] = {
  {0, 10, 2, 6}, {0, 9, 3, 7}, {1, 38, 23, 21}, {1, 37, 22, 32}
};
constexpr uint8_t PACKETS_PER_READ = 24;
constexpr uint32_t QUEUE_SIZE = 1024;
constexpr uint32_t HISTORY_SIZE = 128;
static_assert(ODR_HZ==16000 && ODR_CODE==2 && GYRO_FS==0 && ACCEL_FS==0 && UI_BW==0x11,
  "Changing sampling/ranges also requires updating metadata, scheduling and decoder scaling");
}
