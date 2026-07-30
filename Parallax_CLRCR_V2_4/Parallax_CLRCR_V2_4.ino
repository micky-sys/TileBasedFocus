 /*
 * Title: MovePositionAbsolute
 *
 * Objective:
 *    This example demonstrates control of a ClearPath motor in Step and
 *    Direction mode.
 *

 */

#include "ClearCore.h"
#include <math.h>

#define motor0 ConnectorM0  //Vertical camera motor
#define motor1 ConnectorM1  //Oblique camera motor

// Select the baud rate to match the target serial device, declare COM-0 to not be a TTL input, but an RS232 port
#define baudRate 115200

#define HANDLE_ALERTS (1)

#define TrigInterrupt DI6  //Input signal from IP2, "PLX trigger", 3.3V
#define camTrigPin IO1  //Output to cameras. Comes out both as 3.3V and 24V to two different pins on the camera connector (IN1 and IN2)
#define eventPin IO0 //Output to GNSS, "MID_EXP", 3.3V
#define HB1Select IO3  //H-bridge selection pin 1, for focus mechanisms
#define HB2Select IO2 //H-bridge selection pin 2
#define focusMotorPin ConnectorIO5  //H-bridge output for focus motor
#define encoderPort ConnectorCOM1  //AMT203S absolute encoder (SPI master) on the focus-motor shaft. COM-0 is the RS232 collection link; COM-1 was free.

// AMT20-series (AMT203S) SPI protocol -- see the Same Sky AMT20 datasheet. 12-bit
// absolute (0..4095), SPI mode 0, data rate <= 1 MHz, and the CS line MUST be
// released after EVERY byte. Command: send rd_pos, clock nop bytes while the
// encoder replies with the 0xA5 wait byte, then it echoes rd_pos and the next two
// bytes are MSB (low 4 bits valid) + LSB of the position.
const uint8_t ENC_NOP = 0x00;      // no-op / wait byte
const uint8_t ENC_RD_POS = 0x10;   // read-position command (also its echo)
const int ENC_SPI_HZ = 500000;     // 500 kHz -- comfortably under the 1 MHz max
const int ENC_ECHO_TRIES = 20;     // max nop reads waiting for the rd_pos echo
// Encoder USB report cadence, gated by mode: the closed-loop AF (M4/M5) needs a
// fresh shaft position every ~100 ms, but streaming that fast in the operational
// modes (M0/M1/...) just floods the host log and USB link for no benefit. So poll
// fast only in the focus modes and merely "every once in a while" elsewhere (a
// liveness heartbeat -- enough for the host to know the encoder is alive).
const uint32_t encoderReportFastMs = 100;   // M4/M5: closed-loop AF position feed
const uint32_t encoderReportSlowMs = 2000;  // other modes: liveness only
uint32_t lastEncoderReport = 0;


/*
float PI = 3.14159265;
float DEG_TO_RAD = PI / 180.0;
float RAD_TO_DEG = 180.0 / PI;
*/
int velocityLimit = 150000; // pulses per sec
int accelerationLimit = 150000000; // pulses per sec^2
int velocityLimitSlow = 1000; // pulses per sec
int accelerationLimitSlow = 2000000; // pulses per sec^2

int Positions = 13;  //Number of pictures in a sweep (set to m5Positions while in mode 5)
int basePositions = 13;  //Normal sweep positions (M0/M1/M2/M3)
int m5Positions = 40;  //Mode 5 (ground test) positions per sweep, user-adjustable via $P<n>*
int m5Index = 0;  //Current position index in the non-blocking mode-5 stepped sweep

int inputRes = 6400;  //pulses per rev, as defined in the MSP

float sweepAngleVertical = 60; //SUPPOSED TO BE 60! Total sweep angle, from side to side, for vertical mirror mount - Important note: this is not the motor angle (alpha), this is the angle of the mount itself (theta)
float m5SweepAngle = 10; //Mode-5 (ground test) total sweep angle (deg), user-adjustable via $W<n>*. Kept separate from sweepAngleVertical so leaving M5 restores the 60 deg flight sweep. A narrow angle + many $P positions = small per-step angle so frames overlap and the host patch-tracker can follow.
float sweepAngleOblique = 50;  //Total sweep angle, from side to side, for oblique camera motor
float startAngle1 = -sweepAngleOblique*inputRes/2/360;

bool M0Exists = false;
bool M1Exists = false;
bool SweepNeedsToStart = false;

// Safe-abort: if a motor keeps faulting through repeated enable-cycle clears
// (e.g. the oblique drive faults on every move of a fast sweep), bail out of
// the sweep instead of re-homing/re-faulting forever (which looks like the
// motor "going bonkers"). ponytail: maxMoveFaults is a heuristic (~5 faulted
// moves ≈ 1.5-2 s at the observed ~300 ms/fault); raise it if a genuinely
// marginal-but-recoverable axis trips it, or fix the drive fault at the source.
int consecutiveMoveFaults = 0;
const int maxMoveFaults = 5;
bool sweepAborted = false;  // set by abortToSafeMode() so an in-progress sweep unwinds at once

bool SerialMessageComplete = false;
String SerialMessage;
String incomingSerialMessage;

bool Serial0MessageComplete = false;
String Serial0Message;
String incomingSerial0Message;

int mode = 0;  //Modes of operation. 0 = collection on, based on nav points from Topoflight; 1 = Collection ON, continous running; 2 = Slow and steady, for debugging and previwing
int sweepCount = 0;

int mechDelayFast = 20;
int picDelayFast = 60;
int mechDelaySlow = 400;
int picDelaySlow = 300;
int mechDelayCurr;
int picDelayCurr;
int sweepEndDelay = 500;
int commandDelay = 10;
int minTimeBetweenFrames = 209; //The maximum FPS of the Kaya camera system is 4.8
int picsInPlaceDelay = 250;
int i = 0;

int focusTimeIncrement = 10;
int str0 = 7000;   // extra-fine focus step: duty 7000 (~22%) AT a 3 ms pulse -- the
                   // shortest move that reliably turns the shaft on this rig (below
                   // this it only buzzes). $S0* selects this duty AND drops
                   // focusTimeIncrement to 3; $S1*-$S3* restore the 10 ms increment.
int str1 = 7000;
int str2 = 10000;
int str3 = 14000; 
int focusStrength = str3;  
int focusIncCount = 0;
int ackNumber = 0;

// Non-blocking DISCRETE focus-pulse train. A '+'/'-' command of length N is
// executed as N SEPARATE pulses -- H-bridge ON for focusTimeIncrement ms, then
// OFF for focusPulseGapMs ms -- NOT one continuous N*inc drive.
//
// Why discrete: a continuous burst let the motor ACCELERATE to several rev/s,
// which (a) made "encoder counts per pulse" nonlinear (a burst covered far more
// than N x the single-pulse step) and (b) spun the shaft PAST half a revolution
// between the 10 Hz $ENC samples, so the host's 12-bit absolute-encoder unwrap
// aliased and silently dropped whole revolutions (the count "barely moved" while
// the lens swept through focus). Pulsing discretely lets the shaft coast to rest
// in each gap, so every pulse is ~one calibrated increment and the encoder can
// never alias. serviceFocusPulse() sequences the train from loop() without
// blocking, so the camera trigger keeps firing on schedule.
//
// Bursts QUEUE (they do NOT replace each other): the host sends multi-leg moves
// as back-to-back commands (e.g. park_upward = a '-' dive immediately followed by
// a '+' seat). A "latest-command-wins" handler silently drops the first leg -- the
// dive never happened and the motor only did the seat (drove the WRONG way into
// the end stop). A small FIFO plays each burst to completion in order instead.
const int FOCUS_Q_LEN = 8;            // pending-burst ring buffer depth
int focusQDir[FOCUS_Q_LEN];           // +1 / -1 per queued burst
int focusQCount[FOCUS_Q_LEN];         // pulse count per queued burst
int focusQHead = 0;                   // index of the next burst to play
int focusQTail = 0;                   // index of the next free slot (head==tail => empty)
enum FocusPhase { FP_IDLE, FP_ON, FP_GAP };
FocusPhase focusPhase = FP_IDLE;      // where the current burst is in its ON/GAP cycle
int focusPulseDir = 0;                // +1 / -1 drive direction of the burst in progress
int focusPulsesRemaining = 0;         // pulses left AFTER the current one in this burst
uint32_t focusPhaseEndTime = 0;       // millis() deadline for the current ON or GAP phase
const uint32_t focusPulseGapMs = 12;  // OFF time between pulses -- lets the shaft settle so each pulse is ~one increment (tune up if the motor still coasts across pulses)

int serial0input;

uint32_t timeout = 8000; //Reduced from 18000 
uint32_t startTime;
uint32_t sweepStartTime;
long lastSweepTime;
uint32_t lastFrameTime;

// Vertical camera mechanism parameters
float theta_deg = 0; //Mirror mount angle, in degrees
float alpha_deg = 0; //Motor arm angle, in degrees
float theta = 0;  //Mirror mount angle, radians
float alpha = 0;  //Motor arm angle, radians
float l = 45.0;  //Motor arm length
float L = 80.0;  //Mirror mount arm length
float H = 157.4;  //Distance between motor shaft and mirror mount shaft
float C = 165.3;  //Conrod length.
float verticalAngles[64];  //Sized for the largest supported sweep (mode-5 ground test, up to 64 positions)

// Convergence parameters for numerical calculation
const float TOLERANCE = 0.0001;
const int MAX_ITER = 100;

bool Command = false;

void setup() {
    M0Exists = false;
    M1Exists = false;
    SweepNeedsToStart = false;
    SerialMessageComplete = false;
    mode = 0;

    pinMode(TrigInterrupt, INPUT);
    pinMode(eventPin, OUTPUT);
    pinMode(camTrigPin, OUTPUT);
    pinMode(HB1Select, OUTPUT);
    pinMode(HB2Select, OUTPUT);
    focusMotorPin.Mode(Connector::OUTPUT_H_BRIDGE);  //H-bridge for focus motor
    digitalWrite(camTrigPin, true); //optocoupler outputs are reversed so it puts the camera trigger signal to low (0V)
    digitalWrite(eventPin, true);  //same for Gamad events
    digitalWrite(HB1Select, false); // 0 at HB1Select sets the focus mechanism selection to camera 1, no matter what happens on HB2Select
    digitalWrite(HB2Select, false);  //  1 at HB1Select and 0 at HB2 select sets the focus mechanism to cam 2, while 1 and 1 sets to cam 3
    
    focusMotorPin.State(0);
    attachInterrupt(digitalPinToInterrupt(TrigInterrupt), SweepOnTrig, FALLING);
    interrupts();


    // Sets the input clocking rate. This normal rate is ideal for ClearPath
    // step and direction applications.
    MotorMgr.MotorInputClocking(MotorManager::CLOCK_RATE_NORMAL);
    

    // Sets all motor connectors into step and direction mode.
    MotorMgr.MotorModeSet(MotorManager::MOTOR_ALL,
                          Connector::CPM_MODE_STEP_AND_DIR);

    // Set the motor's HLFB mode to bipolar PWM
    motor0.HlfbMode(MotorDriver::HLFB_MODE_HAS_BIPOLAR_PWM);
    motor1.HlfbMode(MotorDriver::HLFB_MODE_HAS_BIPOLAR_PWM);
    // Set the HFLB carrier frequency to 482 Hz
    motor0.HlfbCarrier(MotorDriver::HLFB_CARRIER_482_HZ);
    motor1.HlfbCarrier(MotorDriver::HLFB_CARRIER_482_HZ);

    // Sets the maximum velocity for each move
    motor0.VelMax(velocityLimit);
    motor1.VelMax(velocityLimit);

    // Set the maximum acceleration for each move
    motor0.AccelMax(accelerationLimit);
    motor1.AccelMax(accelerationLimit);

    // Sets up serial communication and waits up to 5 seconds for a port to open.
    // Serial communication is not required for this example to run.
    Serial.begin(baudRate);
    int SerialTimeout = 5000;
    startTime = millis();
    while (!Serial && millis() - startTime < SerialTimeout) {
        continue;
    }
    // Set up serial communication to send serial input over.
    startTime = millis();
    Serial0.begin(baudRate);
    Serial0.ttl(false);
    while (!Serial0 && millis() - startTime < SerialTimeout) {
        continue;
    }

    // AMT203S absolute encoder on COM-1 as an SPI master (mode 0 per the AMT20
    // datasheet: idle-low clock, sample on the leading edge).
    // ponytail: mode 0 is what the datasheet specifies; if the bench read comes
    // back as garbage/stuck, flip the phase to TRAIL_SAMPLE (mode 1) -- that pair
    // is the only clocking knob to try, and I can't verify it without the hardware.
    encoderPort.Mode(Connector::SPI);
    encoderPort.Speed(ENC_SPI_HZ);
    encoderPort.SpiClock(SerialDriver::SCK_LOW, SerialDriver::LEAD_SAMPLE);
    encoderPort.PortOpen();

    calculateMotorAngles ();

    // Enables the motor; homing will begin automatically if enabled
    EnableAndHome ();
    Serial.print("M0Exists is  ");
    Serial.println(M0Exists);
    Serial.print("M1Exists is  ");
    Serial.println(M1Exists);

	// Check if motor alert occurred during enabling
	// Clear alert if configured to do so 
    if (M0Exists) {  
      if (motor0.StatusReg().bit.AlertsPresent) {
        Serial.println("Motor 0 alert detected.");		
        PrintAlerts();
        if(HANDLE_ALERTS){
          HandleAlerts();
      } else {
        Serial.println("Enable automatic alert handling by setting HANDLE_ALERTS to 1.");
        }
        Serial.println("Enabling may not have completed as expected. Proceed with caution.");		
        Serial.println();
      } else {
      Serial.println("Motor 0 Ready");	
      }
    }
  
    if (M1Exists) {  
      if (motor1.StatusReg().bit.AlertsPresent) {
        Serial.println("Motor 1 alert detected.");		
        PrintAlerts();
        if(HANDLE_ALERTS){
          HandleAlerts();
      } else {
        Serial.println("Enable automatic alert handling by setting HANDLE_ALERTS to 1.");
        }
        Serial.println("Enabling may not have completed as expected. Proceed with caution.");		
        Serial.println();
      } else {
      Serial.println("Motor 1 Ready");	
      }
    }

  setMode(0);  //Put the system in mode 0

  Serial0.println("$Homing completed successfully*");
}

void loop() {
  switch (mode) {
    case 0:
      if (SweepNeedsToStart) { 
        Sweep();
        SweepNeedsToStart=false;
      }
      break;
    case 1:
      Sweep();
      break;  
    case 2:
      Sweep();
      break; 
    case 3:
      if (SweepNeedsToStart) { 
        Sweep();
      }
      break;
    case 4:
      TakePicsInPlace();
      break;
    case 5:
      SweepStepM5();
      break;
    case 6:
      TakePicsInPlace();
      break;
  }

    serviceFocusPulse();

    CheckMessages();

    reportEncoder();

}

 void SweepOnTrig(){
    SweepNeedsToStart=true;
 }

// Enqueue a discrete N-pulse burst in direction dir (+1/-1). serviceFocusPulse()
// plays it (and any already queued ahead of it) to completion, in order. Dropped
// if the queue is full -- the host paces itself via settle-and-verify, so that
// only happens under a runaway command stream.
void startFocusBurst(int dir, int count){
  if (count <= 0) return;
  int next = (focusQTail + 1) % FOCUS_Q_LEN;
  if (next == focusQHead) return;   // queue full: drop
  focusQDir[focusQTail] = dir;
  focusQCount[focusQTail] = count;
  focusQTail = next;
}

// Advance the non-blocking discrete focus-pulse FIFO: when idle, dequeue the next
// burst; within a burst, alternate ON (focusTimeIncrement ms) and GAP
// (focusPulseGapMs ms) for each pulse; a trailing GAP separates back-to-back
// bursts. Force-stops and flushes if the mode leaves M4/M5. Cheap enough to also
// call inside the trigger busy-waits so pulses sequence promptly instead of
// waiting for the next loop(). Wrap-safe deadline compare via signed subtraction.
void serviceFocusPulse(){
  if (mode != 4 && mode != 5){                  // left focus mode: stop and flush the queue
    focusMotorPin.State(0);
    focusPhase = FP_IDLE;
    focusPulsesRemaining = 0;
    focusQHead = focusQTail = 0;
    return;
  }
  if (focusPhase == FP_IDLE){
    if (focusQHead == focusQTail) return;       // nothing queued
    focusPulseDir = focusQDir[focusQHead];      // dequeue the next burst
    focusPulsesRemaining = focusQCount[focusQHead] - 1;   // this first pulse fires now
    focusQHead = (focusQHead + 1) % FOCUS_Q_LEN;
    focusMotorPin.State(focusPulseDir > 0 ? focusStrength : -focusStrength);
    focusPhase = FP_ON;
    focusPhaseEndTime = millis() + (uint32_t)focusTimeIncrement;
    return;
  }
  if ((int32_t)(millis() - focusPhaseEndTime) < 0) return;  // current phase still running
  if (focusPhase == FP_ON){
    focusMotorPin.State(0);                                 // pulse done -> OFF gap
    focusPhase = FP_GAP;
    focusPhaseEndTime = millis() + focusPulseGapMs;
  } else {                                                  // GAP done
    if (focusPulsesRemaining > 0){
      focusPulsesRemaining--;
      focusMotorPin.State(focusPulseDir > 0 ? focusStrength : -focusStrength);
      focusPhase = FP_ON;
      focusPhaseEndTime = millis() + (uint32_t)focusTimeIncrement;
    } else {
      focusPhase = FP_IDLE;   // burst complete (incl. trailing gap) -> next call pulls the next
    }
  }
}

void CheckMessages() {
  //Check if there's an incoming message, if so read it  
  checkSerialMessage();
  checkSerial0Message();   
    
  if (SerialMessageComplete) {
    ackNumber++;
    Serial.print("ack: ");
    Serial.print(incomingSerialMessage);
    Serial.print (";  A# ");
    Serial.println (ackNumber);
   if (incomingSerialMessage.startsWith("$")){
      if (incomingSerialMessage.equals("$stop*")){
       stop();            
      }
      if (incomingSerialMessage.equals("$M0*")){  //Mode 0, which is nav point based (similar to CollectionOn). Waits for interrupt on DI-6 to start a sweep
        setMode(0);
      }
      if (incomingSerialMessage.equals("$M1*")){  //Mode 1, which is a fast constant sweep (Similar to CollectionON). Starts right away, with a delay of SweepEndDelay between sweeps
        setMode(1);
      }
      if (incomingSerialMessage.equals("$M2*")){  //Mode 2, which is a slow and steady sweep. This is used for tests and image quality assessment in flight
        setMode(2);
      }
      if (incomingSerialMessage.equals("$M3*")){  //Mode 3, which is a manual Continous/stop mode
        setMode(3);
      } 
      if (incomingSerialMessage.equals("$M4*")){  //Mode 4, which is a focus adjustmet mode, allowing control of H-bridge driven focus motors. Locks servos in mid position and fires
        setMode(4);
      }
      if (incomingSerialMessage.equals("$M5*")){  //Mode 5, ground test: continuous stepped sweep with host autofocus running in the background (focus +/- serviced between frames)
        setMode(5);
      }
      if (incomingSerialMessage.equals("$M6*")){  //Mode 6, timelapse: mirrors in mid position (like M4) firing once per second, host records full-res (like M0/M1)
        setMode(6);
      }
      if (incomingSerialMessage.startsWith("$P")){  //Set mode-5 (ground test) positions per sweep, e.g. "$P40*"
        int requested = incomingSerialMessage.substring(2).toInt();  //toInt stops at the trailing '*'
        if (requested < 2) { requested = 2; }
        if (requested > 64) { requested = 64; }  //clamp to verticalAngles[] size
        m5Positions = requested;
        Serial.print(" M5 positions:");
        Serial.println(m5Positions);
        if (mode==5) {  //apply live: recompute angles and restart the stepped sweep
          Positions = m5Positions;
          calculateMotorAngles();
          m5Index = 0;
        }
      }
      if (incomingSerialMessage.startsWith("$W")){  //Set the mode-5 ground-test sweep angle (deg), e.g. "$W6*". A narrow sweep + many $P positions = small per-step angle so consecutive frames overlap and the host patch-tracker can follow (a wide 60 deg sweep jumps ~1.5 deg/frame and tracking fails). Leaving M5 restores the 60 deg flight sweep.
        float requested = incomingSerialMessage.substring(2).toFloat();  //toFloat stops at the trailing '*'
        if (requested < 1) { requested = 1; }
        if (requested > 60) { requested = 60; }
        m5SweepAngle = requested;
        Serial.print(" M5 sweep angle (deg):");
        Serial.println(m5SweepAngle);
        if (mode==5) {  //apply live: recompute angles and restart the stepped sweep
          calculateMotorAngles();
          m5Index = 0;
        }
      }
      if (incomingSerialMessage.equals("$T*")){  //Mode 4, which is a focus adjustmet mode, allowing control of H-bridge driven focus motors. Locks servos in mid position and fires
        testClick();
      }
      if (incomingSerialMessage.startsWith("$FT")){  //Focus pulse time-increment in ms per '+'/'-' char, e.g. "$FT1*". Default 10 (normal AF). The backlash test drops it to 1 so it can scan sub-10ms open times to find the minimum move; it restores 10 afterwards. Clamped [1,100].
        int reqInc = incomingSerialMessage.substring(3).toInt();  //toInt stops at the trailing '*'
        if (reqInc < 1) { reqInc = 1; }
        if (reqInc > 100) { reqInc = 100; }
        focusTimeIncrement = reqInc;
        Serial.print("Focus time increment ");
        Serial.println(focusTimeIncrement);
      }
      if (incomingSerialMessage.startsWith("$SV")){  //Variable focus duty for the backlash/min-duty test, e.g. "$SV1200*". Lets the host drive GENTLER than str0 (below stiction) and ramp up to find the minimum duty that actually turns the shaft. Clamped to [0, str3].
        int requested = incomingSerialMessage.substring(3).toInt();  //toInt stops at the trailing '*'
        if (requested < 0) { requested = 0; }
        if (requested > str3) { requested = str3; }  //never exceed the coarsest fixed level
        focusStrength = requested;
        Serial.print("Focus duty ");
        Serial.println(focusStrength);
      }
      else if (incomingSerialMessage.equals("$S0*")){  //Extra-fine focus step: duty str0 (7000) at a 3 ms pulse -- the shortest reliable move.
        Serial.println("Focus strength 0");
        focusStrength = str0;
        focusTimeIncrement = 3;  //fine step is a 3 ms pulse
      }
      if (incomingSerialMessage.equals("$S1*")){  //Mode 6, which is a slow "Take pics in place", for manual focus test
        Serial.println("Focus strength 1");
        focusStrength = str1;
        focusTimeIncrement = 10;  //coarser steps use the normal 10 ms increment
      }  
      if (incomingSerialMessage.equals("$S2*")){  //Mode 6, which is a slow "Take pics in place", for manual focus test
        Serial.println("Focus strength 2");
        focusStrength = str2;
        focusTimeIncrement = 10;
      }      
      if (incomingSerialMessage.equals("$S3*")){  //Mode 6, which is a slow "Take pics in place", for manual focus test
        Serial.println("Focus strength 3");
        focusStrength = str3;
        focusTimeIncrement = 10;
      }
      if (incomingSerialMessage.equals("$C1*")){  //Mode 6, which is a slow "Take pics in place", for manual focus test
        Serial.println("Camera 1 set");
        digitalWrite(HB1Select, false); // 0 at HB1Select sets the focus mechanism selection to camera 1, no matter what happens on HB2Select
        digitalWrite(HB2Select, false);       
      } 
      if (incomingSerialMessage.equals("$C2*")){  //Mode 6, which is a slow "Take pics in place", for manual focus test
        Serial.println("Camera 2 set");
        digitalWrite(HB1Select, true); // 0 at HB1Select sets the focus mechanism selection to camera 1, no matter what happens on HB2Select
        digitalWrite(HB2Select, false);       
      } 
      if (incomingSerialMessage.equals("$C3*")){  //Mode 6, which is a slow "Take pics in place", for manual focus test
        Serial.println("Camera 3 set");
        digitalWrite(HB1Select, true); // 0 at HB1Select sets the focus mechanism selection to camera 1, no matter what happens on HB2Select
        digitalWrite(HB2Select, true);       
      }  

      if (incomingSerialMessage.equals("$reset*")){  //Resets. This includes cycling enable and rehoming. If motors had issues on startup this may solve it
        setup();
      }
      if (incomingSerialMessage.startsWith("$Click")){
       SweepNeedsToStart=true;          
      }
                  
    }
    if (incomingSerialMessage.startsWith("+")){  //Sends pulse command to currently selected focus motor in positive direction
      Serial.println(mode);
      if (mode==4 || mode==5) {
        // N chars => N discrete pulses (on/gap), sequenced by serviceFocusPulse().
        startFocusBurst(+1, incomingSerialMessage.length());
      }
    } 
    if (incomingSerialMessage.startsWith("-")){  //Sends pulse command to currently selected focus motor in negative direction
      if (mode==4 || mode==5) {
        startFocusBurst(-1, incomingSerialMessage.length());
      }
    }
    if (incomingSerialMessage.startsWith(">")){  //Lengthens time between pics in mode 4/5
      if (mode==4 || mode==5) {
        picsInPlaceDelay = picsInPlaceDelay +50;
        if (picsInPlaceDelay > 1500) {
          picsInPlaceDelay = 1500;
        }
        Serial.print(" PicDelay:");
        Serial.println(picsInPlaceDelay);
      }      
    }
    if (incomingSerialMessage.startsWith("<")){  //Shortens time between pics, down to the camera's max FPS (minTimeBetweenFrames)
      if (mode==4 || mode==5) {
        picsInPlaceDelay = picsInPlaceDelay -50;
        if (picsInPlaceDelay<minTimeBetweenFrames) {
          picsInPlaceDelay = minTimeBetweenFrames;
        }
        Serial.print(" PicDelay:");
        Serial.println(picsInPlaceDelay);
      }      
    }
    if (incomingSerialMessage.startsWith("?")){  //Send command in Mode 7
      if (mode==3) {
        SweepNeedsToStart=true;
        Serial.println("Line start");
      }      
    }
    if (incomingSerialMessage.startsWith("!")){  //Send command in Mode 7
      if (mode==3) {
        SweepNeedsToStart=false;
        Serial.println("Line end");
      }      
    }
    SerialMessageComplete=false;
  }

  if (Serial0MessageComplete) {
    Serial.print("Serial 0 message: ");
    Serial.println(incomingSerial0Message);
   if (incomingSerial0Message.startsWith("$")){
      if (incomingSerial0Message.equals("$Collectionon")){
       if (mode != 0){
        setMode(0);
       }
       Serial0.println("$Done*");            
      }
      if (incomingSerial0Message.startsWith("$Click")){
       SweepNeedsToStart=true;          
      }
      if (incomingSerial0Message.startsWith("$CollectionON")){
       setMode(1); 
       Serial0.println("$Done*");            
      }
      if (incomingSerial0Message.startsWith("$CollectionOff")){
       if (mode != 0){
        setMode(0);
       } 
       Serial0.println("$Done*");            
      }
      if (incomingSerial0Message.startsWith("$CollectionOFF")){
       stop();  
       Serial0.println("$Done*");            
      } 
      if (incomingSerial0Message.startsWith("$Sensor=")){
       Serial0.println("$Ready*");            
      }                            
    }
 
    Serial0MessageComplete=false;
  }
}

void checkSerialMessage (){
  while (Serial.available()) {
    // get the new byte:
    char inChar = (char)Serial.read();
    // check if new line, and if not, add it to the message:    
    if (inChar == '\n') {
      SerialMessageComplete = true;
      incomingSerialMessage=SerialMessage;
      SerialMessage="";
      // Process exactly ONE complete message per call. Any further bytes
      // already buffered (e.g. the host sends "$M4*" immediately followed
      // by "$C2*" when entering focus mode) stay in the serial buffer and
      // are handled on the next loop. Draining the whole buffer here would
      // let a trailing command overwrite this one before CheckMessages()
      // ever acts on it -- which dropped the $M4* whenever it arrived mid
      // sweep, leaving the mirror running.
      return;
    }
    else {
    SerialMessage += inChar;
    }
  } 
}

void checkSerial0Message () {
  serial0input = Serial0.read();
  // If there was a valid byte read-in, print it.
  if (serial0input != -1) {
    char inChar = (char)serial0input;
    if (inChar == '*') {
      Serial0MessageComplete = true;
      incomingSerial0Message=Serial0Message;
      Serial0Message="";
    }
    else {
      Serial0Message += inChar;
    }
  }
}

// One SPI byte to the AMT203S, releasing CS afterwards -- the AMT20 datasheet
// requires the CS line be released after EVERY byte, not just per transaction.
uint8_t encoderTransfer(uint8_t sendByte) {
  encoderPort.SpiSsMode(SerialDriver::LINE_ON);   // assert CS (active low)
  uint8_t data = encoderPort.SpiTransferData(sendByte);
  encoderPort.SpiSsMode(SerialDriver::LINE_OFF);  // release CS
  delayMicroseconds(10);                          // datasheet inter-byte guard
  return data;
}

// Read the 12-bit absolute position (0..4095) from the AMT203S focus-shaft
// encoder. Returns -1 if the encoder never echoes rd_pos within the retry budget
// (e.g. not connected, wiring/level-shift fault) so a bad read is distinguishable
// from a real 0 position.
int readEncoderPosition() {
  uint8_t data = encoderTransfer(ENC_RD_POS);
  int tries = 0;
  while (data != ENC_RD_POS && tries++ < ENC_ECHO_TRIES) {
    data = encoderTransfer(ENC_NOP);  // encoder returns 0xA5 while it is busy
  }
  if (data != ENC_RD_POS) {
    return -1;  // no acknowledgement -> read failed
  }
  int pos = (encoderTransfer(ENC_NOP) & 0x0F) << 8;  // MSB: only low 4 bits valid
  pos |= encoderTransfer(ENC_NOP);                    // LSB
  return pos;
}

// Poll the focus-shaft encoder and stream its absolute position to the host over
// USB at a fixed cadence. Tagged "$ENC=<pos>*" so the capture app can parse it
// without confusing it with the human-readable ack/debug lines; pos = -1 on a
// failed read. Cadence is mode-gated (see encoderReportFast/SlowMs): fast in the
// focus modes M4/M5 where AF consumes it, slow elsewhere so it doesn't spam the
// log. (The blocking sweep modes also only run loop() between sweeps.)
void reportEncoder() {
  uint32_t interval = (mode == 4 || mode == 5) ? encoderReportFastMs : encoderReportSlowMs;
  if (millis() - lastEncoderReport < interval) {
    return;
  }
  lastEncoderReport = millis();
  int pos = readEncoderPosition();
  Serial.print("$ENC=");
  Serial.print(pos);
  Serial.println("*");
}

void EnableAndHome () { 
  //Cycle enable. This is defined in MSP to trigger a homing sequence
  motor0.EnableRequest(false);
  motor1.EnableRequest(false);  
  delay (30);
  motor0.EnableRequest(true);
  motor1.EnableRequest(true);   
  delay(commandDelay); 

  Serial.println("entering homing sequence");
  startTime = millis(); //Start time for homing sequence timeout

  if ((M0Exists && M1Exists) || (!M1Exists && !M0Exists)) {  //This happens on startup or when both motors are connected
    while (((motor0.HlfbState() != MotorDriver::HLFB_ASSERTED) || (motor1.HlfbState() != MotorDriver::HLFB_ASSERTED))&&((millis()-startTime)<timeout))  //Wait until both motors have homed, or timeout has passed
    {
      continue;
    }
    Serial.print("  home wait "); Serial.print(millis() - startTime); Serial.println(" ms");
    //See which motor has homed, if at all
    bool m0Homed = (motor0.HlfbState() == MotorDriver::HLFB_ASSERTED);
    bool m1Homed = (motor1.HlfbState() == MotorDriver::HLFB_ASSERTED);
    if (m0Homed){
      M0Exists=true;
      Serial.println("M0 homing success");
    }
    if (m1Homed){
      M1Exists=true;
      Serial.println("M1 homing success");
    }
    // A motor that did NOT assert HLFB within the timeout has FAILED to home.
    // Leaving it enabled lets the ClearPath keep running its homing move -- the
    // oblique "hunting endlessly / going bonkers". Disable it so the drive
    // aborts the homing motion and holds still. (Disabling an absent motor is a
    // harmless no-op.) If exactly ONE motor homed, the other is a real
    // connected-but-faulted drive, so also flag the host.
    if (!m0Homed) {
      motor0.EnableRequest(false);
      Serial.println("M0 homing FAILED - motor disabled to stop homing motion");
    }
    if (!m1Homed) {
      motor1.EnableRequest(false);
      Serial.println("M1 homing FAILED - motor disabled to stop homing motion");
    }
    if (m0Homed != m1Homed) {
      Serial0.println("$Fault*");
    }
    if (!m0Homed && !m1Homed) {
      Serial.println("Motor homing failure");
    }
    if (M0Exists || M1Exists) {
      Serial0.println("$Ready*");
    }
  }

  if (M0Exists && !M1Exists) {  //This happens if it's known only vertical motor is connected
    while (((motor0.HlfbState() != MotorDriver::HLFB_ASSERTED))&&((millis()-startTime)<timeout))
    {
      continue;
    }
    if (motor0.HlfbState() == MotorDriver::HLFB_ASSERTED){
      Serial.println("M0 homing success");
      Serial0.println("$Ready*");
      return;
    }
    motor0.EnableRequest(false);  // homing failed within timeout -> stop the homing motion
    Serial.println("M0 homing FAILED - motor disabled to stop homing motion");
    Serial0.println("$Fault*");
  }
  
  if (M1Exists && !M0Exists) {  //This happens if it's known only oblique motor is connected
    while (((motor1.HlfbState() != MotorDriver::HLFB_ASSERTED))&&((millis()-startTime)<timeout))
    {
      continue;
    }
    if (motor1.HlfbState() == MotorDriver::HLFB_ASSERTED){
      Serial.println("M1 homing success");
      Serial0.println("$Ready*");
      return;
    }
    motor1.EnableRequest(false);  // homing failed within timeout -> stop the homing motion
    Serial.println("M1 homing FAILED - motor disabled to stop homing motion");
    Serial0.println("$Fault*");
  }
}

void Sweep(){
  sweepStartTime=millis();

  for (i=0; i<Positions; i++){
    int position0 = int(verticalAngles[i]*6400/360);
    int position1 = int(sweepAngleOblique*inputRes*i/(360*(Positions-1))+startAngle1);
    MoveAbsolutePosition(position0, position1);
    if (sweepAborted) { sweepAborted = false; return; }  // safe-abort: stop this sweep now
    delay(mechDelayCurr);
    while ((millis()-lastFrameTime)<minTimeBetweenFrames){

    }
    digitalWrite(camTrigPin, false); //optocoupler outputs are reversed so it puts the camera trigger signal to high (3.3V)
    digitalWrite(eventPin, false);
    lastFrameTime = millis();
    delay (picDelayCurr);
    digitalWrite(camTrigPin, true); //optocoupler outputs are reversed so it puts the camera trigger signal to low (0V)
    digitalWrite(eventPin, true);
    }
  calculateSweepTime();
  Serial0.println("$Done*");  
  MoveAbsolutePosition(verticalAngles[0]*6400/360,startAngle1);
  Serial0.println("$Ready*");
}

void SweepStepM5(){
  //Ground-test mode 5: a NON-BLOCKING stepped sweep. Each call moves the mirrors
  //to exactly one position, settles, and fires one frame, then RETURNS so the
  //main loop can run CheckMessages() and apply any host autofocus +/- pulses
  //before the next move. This is what lets the focus algorithm run "in the
  //background" while the mirrors traverse the FOV to simulate a moving scene.
  //Positions == m5Positions here; m5Index walks 0..Positions-1 and wraps
  //(continuous). In-position is verified by MoveAbsolutePosition (StepsComplete +
  //HLFB), then mechDelayCurr gives the mirror time to settle.
  if (m5Index == 0) {
    sweepStartTime = millis();
  }
  int position0 = int(verticalAngles[m5Index]*6400/360);
  int position1 = int(sweepAngleOblique*inputRes*m5Index/(360*(Positions-1))+startAngle1);
  MoveAbsolutePosition(position0, position1);
  if (sweepAborted) { sweepAborted = false; return; }  // safe-abort: stop this stepped sweep now
  delay(mechDelayCurr);
  while ((millis()-lastFrameTime)<minTimeBetweenFrames){
    serviceFocusPulse();  // stop a finished focus pulse without waiting for next loop()
  }
  digitalWrite(camTrigPin, false); //optocoupler outputs are reversed so it puts the camera trigger signal to high (3.3V)
  digitalWrite(eventPin, false);
  lastFrameTime = millis();
  delay (picDelayCurr);
  digitalWrite(camTrigPin, true); //optocoupler outputs are reversed so it puts the camera trigger signal to low (0V)
  digitalWrite(eventPin, true);

  m5Index++;
  if (m5Index >= Positions) {
    m5Index = 0;  //continuous sweep
    calculateSweepTime();
  }
}

void TakePicsInPlace(){
    //Taking pictures continuosly in mid position.
    //picsInPlaceDelay is the trigger-to-trigger period (ms), gated the same way
    //as the sweep so M4 can run right up to the camera's max FPS
    //(minTimeBetweenFrames) for autofocus, instead of adding a fixed trailing
    //delay on top of the trigger pulse.
    while ((millis()-lastFrameTime)<picsInPlaceDelay){
      serviceFocusPulse();  // stop a finished focus pulse without waiting for next loop()
    }
    digitalWrite(camTrigPin, false); //optocoupler outputs are reversed so it puts the camera trigger signal to high (3.3V)
    lastFrameTime = millis();
    delay (picDelayCurr);
    digitalWrite(camTrigPin, true); //optocoupler outputs are reversed so it puts the camera trigger signal to low (0V)
    MoveAbsolutePosition(0, 0);
}

void testClick(){
    //Takes a single picture, without disturbing the motors
    digitalWrite(camTrigPin, false); //optocoupler outputs are reversed so it puts the camera trigger signal to high (3.3V)
    delay (picDelayCurr);
    digitalWrite(camTrigPin, true); //optocoupler outputs are reversed so it puts the camera trigger signal to low (0V)
    delay(picsInPlaceDelay);
}

void stop() {
  MoveAbsolutePosition(0, 0);
  Serial.println("sending disable");
  motor0.EnableRequest(false);
  motor1.EnableRequest(false);
  Serial.println("disable sent");
  mode = 0;
  i=0;
  Serial.println("Stopping");
}

void setupMotorsFast() {
 motor0.VelMax(velocityLimit);
 motor1.VelMax(velocityLimit);
 motor0.AccelMax(accelerationLimit);
 motor1.AccelMax(accelerationLimit);
 mechDelayCurr = mechDelayFast;
 picDelayCurr = picDelayFast;
}

void setupMotorsSlow() {
  motor0.VelMax(velocityLimitSlow);
  motor1.VelMax(velocityLimitSlow);
  motor0.AccelMax(accelerationLimitSlow);
  motor1.AccelMax(accelerationLimitSlow);
  mechDelayCurr = mechDelaySlow;
  picDelayCurr = picDelaySlow;
}

 void setMode(int switchToMode) {
  SweepNeedsToStart=false;
  stop();
  EnableAndHome ();
  switch (switchToMode) {
    case 0: 
     mode = 0;
     Serial.println("Mode 0: Collection On, Nav point based");          
     Positions = basePositions;
     calculateMotorAngles();
     setupMotorsFast();
    break;
    case 1:
     mode = 1;
     Serial.println("Mode 1: Collection ON, continous");          
     Positions = basePositions;
     calculateMotorAngles();
     setupMotorsFast();
    break;
    case 2:
      mode = 2;
      Serial.println("Mode 2: Slow and Steady");
      Positions = basePositions;
      calculateMotorAngles();
      setupMotorsSlow();
    break;
    case 3:
      mode = 3;
      Serial.println("Mode 3: Collection ON Line by line");
      Positions = basePositions;
      calculateMotorAngles();
      setupMotorsFast();
    break;
    case 4:
     mode = 4;
     Serial.println("Mode 4: Focus adjustment");
     setupMotorsFast();
     picsInPlaceDelay = minTimeBetweenFrames;  //AF runs at the camera's max FPS (209 ms floor); reset any leftover (e.g. M6's 1000 ms)
    break;
    case 5:
     mode = 5;
     Serial.println("Mode 5: Ground test (stepped sweep + background autofocus)");
     Positions = m5Positions;
     calculateMotorAngles();
     setupMotorsFast();
     m5Index = 0;
     picsInPlaceDelay = minTimeBetweenFrames;  //AF runs at the camera's max FPS (209 ms floor); reset any leftover
    break;
    case 6:
     mode = 6;
     Serial.println("Mode 6: Timelapse (mid position, 1 Hz, full-res recording)");
     setupMotorsFast();
     picsInPlaceDelay = 1000;  //one trigger per second
    break;
  }
 }

 void calculateMotorAngles () {
  Serial.println("calculating angles");
  float span = (mode==5) ? m5SweepAngle : sweepAngleVertical;  //M5 ground test uses its own narrow, tunable sweep; flight sweeps keep the full 60 deg
  for (i=0; i<Positions; i++){
    theta_deg = (span/2)-i*span/(Positions-1);
    theta = theta_deg * DEG_TO_RAD;
    // Initial guess (radians)
    alpha = 2*theta;
    int iter = 0;
    while (iter < MAX_ITER) {
      float f = l*l + L*L + H*H 
              - 2*l*L*cos(alpha - theta) 
              + 2*l*H*sin(alpha) 
              - 2*H*L*sin(theta) 
              - C*C;

      float df = 2*l*L*sin(alpha - theta) + 2*l*H*cos(alpha); // derivative w.r.t alpha

      float delta = f / df;
      alpha -= delta;
      iter++;

      if (fabs(delta) < TOLERANCE) {
        break;
      }
    }
    alpha_deg = alpha * RAD_TO_DEG; 
    Serial.print (theta_deg);
    Serial.print ("  -->  ");
    Serial.println(alpha_deg);
    verticalAngles[i] = alpha_deg;
  }
 }


bool MoveAbsolutePosition(int position0, int position1) {
    // Check if a motor alert is currently preventing motion
	// Clear alert if configured to do so 
    if (M0Exists && motor0.StatusReg().bit.AlertsPresent) {
		Serial.println("Motor alert detected.");		
		PrintAlerts();
		if(HANDLE_ALERTS){
			HandleAlerts();
		} else {
			Serial.println("Enable automatic alert handling by setting HANDLE_ALERTS to 1.");
		}
		Serial.println("Move canceled.");		
		Serial.println();
        //return false;
    }
    if (M1Exists && motor1.StatusReg().bit.AlertsPresent) {
		Serial.println("Motor alert detected.");		
		PrintAlerts();
		if(HANDLE_ALERTS){
			HandleAlerts();
		} else {
			Serial.println("Enable automatic alert handling by setting HANDLE_ALERTS to 1.");
		}
		Serial.println("Move canceled.");		
		Serial.println();
        //return false;
    }

    // Command the move of absolute distance
    motor0.Move(position0, MotorDriver::MOVE_TARGET_ABSOLUTE);
    motor1.Move(position1, MotorDriver::MOVE_TARGET_ABSOLUTE);
    // Waits for HLFB to assert (signaling the move has successfully completed) for both motors, as long as both exist (if only one exists, then only it)
    delay(commandDelay);
    if (M0Exists && !M1Exists){
        while ( (!motor0.StepsComplete() || motor0.HlfbState() != MotorDriver::HLFB_ASSERTED) && !motor0.StatusReg().bit.AlertsPresent) {       
          continue;
      }
    }
    if (M1Exists && !M0Exists){
        startTime=millis();
        while ((!motor1.StepsComplete() || motor1.HlfbState() != MotorDriver::HLFB_ASSERTED) && (millis() - startTime < timeout)) {       
          continue;
      }
    }
    if (M0Exists && M1Exists){
        while ( ((!motor0.StepsComplete() || motor0.HlfbState() != MotorDriver::HLFB_ASSERTED) && !motor1.StatusReg().bit.AlertsPresent) || ((!motor1.StepsComplete() || motor1.HlfbState() != MotorDriver::HLFB_ASSERTED) && !motor1.StatusReg().bit.AlertsPresent)) {       
          continue;
      }
    }
	// Check if motor alert occurred during move
	// Clear alert if configured to do so 
    if ((M0Exists && motor0.StatusReg().bit.AlertsPresent) || (M1Exists && motor1.StatusReg().bit.AlertsPresent)) {
		Serial.println("Motor alert detected.");		
		PrintAlerts();
		if(HANDLE_ALERTS){
			HandleAlerts();
		} else {
			Serial.println("Enable automatic fault handling by setting HANDLE_ALERTS to 1.");
		}
		Serial.println("Motion may not have completed as expected. Proceed with caution.");
		Serial.println();
		consecutiveMoveFaults++;
		if (consecutiveMoveFaults >= maxMoveFaults) {
			abortToSafeMode();
		}
		return false;
    } else {
		consecutiveMoveFaults = 0;
		//Serial.println("Move Done");
		return true;
	}


}

void abortToSafeMode() {
  // A motor kept faulting through repeated enable-cycle clears. Rather than
  // loop forever re-homing and re-faulting (the "bonkers" oblique behaviour),
  // disable both motors, drop to mode 0 (which does not sweep on its own), and
  // report it so the host/operator can investigate the drive or power-cycle.
  // sweepAborted unwinds any sweep currently in progress (see Sweep/SweepStepM5).
  Serial.println("SAFE ABORT: motor kept faulting; disabling motors, returning to mode 0.");
  Serial0.println("$Fault*");
  motor0.EnableRequest(false);
  motor1.EnableRequest(false);
  mode = 0;
  i = 0;
  SweepNeedsToStart = false;
  sweepAborted = true;
  consecutiveMoveFaults = 0;
}

void calculateSweepTime() {
  sweepCount++;
  int totalSweepTime = millis()-lastSweepTime;
  int netSweepTime = millis()-sweepStartTime;
  lastSweepTime = millis();
  Serial.print ("Swp# ");
  Serial.print (sweepCount);
  Serial.print (";  Total sweep time ");
  Serial.print(totalSweepTime);
  Serial.print (";  Net sweep time "); 
  Serial.println(netSweepTime);
}

 ///**********************SUPPORT FUNCTIONS FROM CLEARCORE*************************//////

 void HandleAlerts(){
	if(M0Exists && motor0.AlertReg().bit.MotorFaulted){
		// if a motor fault is present, clear it by cycling enable
		Serial.println("Faults present. Cycling enable signal to motor to clear faults.");
		motor0.EnableRequest(false);
		Delay_ms(10);
		motor0.EnableRequest(true);
	}
 
 	if(M1Exists && motor1.AlertReg().bit.MotorFaulted){
		// if a motor fault is present, clear it by cycling enable
		Serial.println("Faults present. Cycling enable signal to motor to clear faults.");
		motor1.EnableRequest(false);
		Delay_ms(10);
		motor1.EnableRequest(true);
	}
	// clear alerts
	Serial.println("Clearing alerts.");
	motor0.ClearAlerts();
  Serial.println("Clearing alerts.");
	motor1.ClearAlerts();
 }

 void PrintAlerts(){
	// report status of alerts
 	Serial.println("Alerts present: ");
	if(motor0.AlertReg().bit.MotionCanceledInAlert){
		Serial.println("Motor0:    MotionCanceledInAlert "); }
	if(motor0.AlertReg().bit.MotionCanceledPositiveLimit){
		Serial.println("Motor0:    MotionCanceledPositiveLimit "); }
	if(motor0.AlertReg().bit.MotionCanceledNegativeLimit){
		Serial.println("Motor0:    MotionCanceledNegativeLimit "); }
	if(motor0.AlertReg().bit.MotionCanceledSensorEStop){
		Serial.println("Motor0:    MotionCanceledSensorEStop "); }
	if(motor0.AlertReg().bit.MotionCanceledMotorDisabled){
		Serial.println("Motor0:    MotionCanceledMotorDisabled "); }
	if(motor0.AlertReg().bit.MotorFaulted){
		Serial.println("Motor0:    MotorFaulted ");
	}
  if(motor1.AlertReg().bit.MotionCanceledInAlert){
		Serial.println("Motor1:    MotionCanceledInAlert "); }
	if(motor1.AlertReg().bit.MotionCanceledPositiveLimit){
		Serial.println("Motor1:    MotionCanceledPositiveLimit "); }
	if(motor1.AlertReg().bit.MotionCanceledNegativeLimit){
		Serial.println("Motor1:    MotionCanceledNegativeLimit "); }
	if(motor1.AlertReg().bit.MotionCanceledSensorEStop){
		Serial.println("Motor1:    MotionCanceledSensorEStop "); }
	if(motor1.AlertReg().bit.MotionCanceledMotorDisabled){
		Serial.println("Motor1:    MotionCanceledMotorDisabled "); }
	if(motor1.AlertReg().bit.MotorFaulted){
		Serial.println("Motor1:    MotorFaulted ");
	}
 }

