/*
 * X-Parallax FlightBridge protocol scaffold for Teensy 4.1.
 * UART Serial1: RX pin 0, TX pin 1, 115200 8N1, through the hub MAX3232.
 * This sketch does NOT implement IMU acquisition or SD recording.
 * ARM, START and HEALTH deliberately return ERR until recorder hooks are implemented.
 */
#include <Arduino.h>
#include <string.h>
#include <stdlib.h>

static char input[1025];
static size_t used = 0;
static bool discardLine = false;
static uint32_t nmeaReceived = 0;

// Replace these hooks with the logger implementation. START must acknowledge only
// after the recording file is open and acquisition is armed. STOP must acknowledge
// only after capture stops and buffered records are committed to storage.
static const char *recorderState() { return "IDLE"; }
static bool recorderArm() { return false; }  // Arm repeated captures on the HARDWARE trigger.
static bool recorderStart() { return false; }  // Not implemented; never fake success.
static bool recorderStop() { return true; }   // Already idle in this protocol-only sketch.
static bool recorderHealthy() { return false; }
static void onNmea(const char *sentence) {
  ++nmeaReceived;
  // Validate/checksum and store the NMEA with the logger's local receive timestamp.
  // GPS arrival time over UART is NOT the camera's exposure timestamp.
  (void)sentence;
}

struct CachedReply { char id[13]; char result[4]; char state[10]; char detail[40]; };
static CachedReply replies[8] = {};
static uint8_t nextReply = 0;

static uint8_t xorChecksum(const char *text) {
  uint8_t value = 0;
  while (*text) value ^= static_cast<uint8_t>(*text++);
  return value;
}

static void reply(const char *id, const char *result, const char *state, const char *detail) {
  char payload[240];
  snprintf(payload, sizeof(payload), "XP,1,R,%s,%s,%s,%s", id, result, state, detail);
  char suffix[8];
  snprintf(suffix, sizeof(suffix), "*%02X\r\n", xorChecksum(payload));
  Serial1.print('@'); Serial1.print(payload); Serial1.print(suffix);
}

static bool validId(const char *id) {
  if (!id || strlen(id) != 12) return false;
  for (const char *p = id; *p; ++p)
    if (!((*p >= '0' && *p <= '9') || (*p >= 'a' && *p <= 'f'))) return false;
  return true;
}

static void handleLine(char *line) {
  if (line[0] == '$' || line[0] == '!') { onNmea(line); return; }
  if (line[0] != '@' || strlen(line) > 244) return;
  char *star = strrchr(line, '*');
  if (!star || strlen(star + 1) != 2) return;
  char *end;
  unsigned long expected = strtoul(star + 1, &end, 16);
  if (*end) return;
  *star = 0;
  if (xorChecksum(line + 1) != expected) return;
  char *save;
  char *prefix = strtok_r(line + 1, ",", &save);
  char *version = strtok_r(nullptr, ",", &save);
  char *type = strtok_r(nullptr, ",", &save);
  char *id = strtok_r(nullptr, ",", &save);
  char *command = strtok_r(nullptr, ",", &save);
  if (!prefix || !version || !type || !command || !validId(id)
      || strcmp(prefix, "XP") || strcmp(version, "1") || strcmp(type, "C")
      || strtok_r(nullptr, ",", &save)) return;
  for (const auto &cached : replies) {
    if (!strcmp(cached.id, id)) { reply(id, cached.result, cached.state, cached.detail); return; }
  }
  const char *result = "OK", *detail = "PROTOCOL_ONLY";
  if (!strcmp(command, "ARM")) {
    if (!recorderArm()) { result = "ERR"; detail = "LOGGER_NOT_IMPLEMENTED"; }
    else detail = "ARMED";
  } else if (!strcmp(command, "START")) {
    if (!recorderStart()) { result = "ERR"; detail = "LOGGER_NOT_IMPLEMENTED"; }
    else detail = "STARTED";
  } else if (!strcmp(command, "STOP")) {
    if (!recorderStop()) { result = "ERR"; detail = "STOP_FAILED"; }
    else detail = "STOPPED";
  } else if (!strcmp(command, "HEALTH")) {
    if (!recorderHealthy()) { result = "ERR"; detail = "LOGGER_NOT_IMPLEMENTED"; }
    else detail = "HEALTHY";
  } else if (strcmp(command, "STATUS")) {
    result = "ERR"; detail = "UNKNOWN_COMMAND";
  }
  CachedReply &cached = replies[nextReply++ % 8];
  snprintf(cached.id, sizeof(cached.id), "%s", id);
  snprintf(cached.result, sizeof(cached.result), "%s", result);
  snprintf(cached.state, sizeof(cached.state), "%s", recorderState());
  snprintf(cached.detail, sizeof(cached.detail), "%s", detail);
  reply(cached.id, cached.result, cached.state, cached.detail);
}

void setup() {
  Serial1.begin(115200);
  reply("0", "OK", recorderState(), "BOOT");
}

void loop() {
  while (Serial1.available()) {
    int value = Serial1.read();
    if (value == '\r' || value == '\n') {
      if (used && !discardLine) { input[used] = 0; handleLine(input); }
      used = 0; discardLine = false;
    } else if (!discardLine) {
      if (value < 32 || value > 126 || used >= sizeof(input) - 1) {
        used = 0; discardLine = true;
      } else input[used++] = static_cast<char>(value);
    }
  }
  // Run acquisition/SD state machines here; do not block UART parsing.
}
