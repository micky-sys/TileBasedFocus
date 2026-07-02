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


/*
float PI = 3.14159265;
float DEG_TO_RAD = PI / 180.0;
float RAD_TO_DEG = 180.0 / PI;
*/
int velocityLimit = 150000; // pulses per sec
int accelerationLimit = 150000000; // pulses per sec^2
int velocityLimitSlow = 1000; // pulses per sec
int accelerationLimitSlow = 2000000; // pulses per sec^2

int Positions = 13;  //Number of pictures in a sweep

int inputRes = 6400;  //pulses per rev, as defined in the MSP

float sweepAngleVertical = 60; //SUPPOSED TO BE 60! Total sweep angle, from side to side, for vertical mirror mount - Important note: this is not the motor angle (alpha), this is the angle of the mount itself (theta)
float sweepAngleOblique = 50;  //Total sweep angle, from side to side, for oblique camera motor
float startAngle1 = -sweepAngleOblique*inputRes/2/360;

bool M0Exists = false;
bool M1Exists = false;
bool SweepNeedsToStart = false;

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
int str1 = 7000;
int str2 = 10000;
int str3 = 14000; 
int focusStrength = str3;  
int focusIncCount = 0;
int ackNumber = 0;

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
float verticalAngles[20];

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
  }

    CheckMessages();

}

 void SweepOnTrig(){
    SweepNeedsToStart=true;
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
      if (incomingSerialMessage.equals("$T*")){  //Mode 4, which is a focus adjustmet mode, allowing control of H-bridge driven focus motors. Locks servos in mid position and fires
        testClick();
      }
      if (incomingSerialMessage.equals("$S1*")){  //Mode 6, which is a slow "Take pics in place", for manual focus test
        Serial.println("Focus strength 1");
        focusStrength = str1;
      }  
      if (incomingSerialMessage.equals("$S2*")){  //Mode 6, which is a slow "Take pics in place", for manual focus test
        Serial.println("Focus strength 2");
        focusStrength = str2;
      }      
      if (incomingSerialMessage.equals("$S3*")){  //Mode 6, which is a slow "Take pics in place", for manual focus test
        Serial.println("Focus strength 3");
        focusStrength = str3;
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
      if (mode==4) {
        focusIncCount = incomingSerialMessage.length();
        focusMotorPin.State(focusStrength);
        for (int i=0; i<focusIncCount; i++){
          delay(focusTimeIncrement);
        }
        focusMotorPin.State(0);
      }
    } 
    if (incomingSerialMessage.startsWith("-")){  //Sends pulse command to currently selected focus motor in negative direction
      if (mode==4) {
        focusIncCount = incomingSerialMessage.length();
        focusMotorPin.State(-focusStrength);
        for (int i=0; i<focusIncCount; i++){
          delay(focusTimeIncrement);
        }
        focusMotorPin.State(0);
      }
    }
    if (incomingSerialMessage.startsWith(">")){  //Lengthens time between pics in mode 4
      if (mode==4) {
        picsInPlaceDelay = picsInPlaceDelay +50;
        if (picsInPlaceDelay > 1500) {
          picsInPlaceDelay = 1500;
        }
        Serial.print(" PicDelay:");
        Serial.println(picsInPlaceDelay);
      }      
    }
    if (incomingSerialMessage.startsWith("<")){  //Shortens time between pics, down to the camera's max FPS (minTimeBetweenFrames)
      if (mode==4) {
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
    //See which motor has homed, if at all
    if (motor0.HlfbState() == MotorDriver::HLFB_ASSERTED){ 
      M0Exists=true;
      Serial.println("M0 homing success");
    }
    if (motor1.HlfbState() == MotorDriver::HLFB_ASSERTED){
      M1Exists=true;
      Serial.println("M1 homing success");
    }
    if ((motor0.HlfbState() != MotorDriver::HLFB_ASSERTED) && (motor1.HlfbState() != MotorDriver::HLFB_ASSERTED)) {
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
  }
}

void Sweep(){
  sweepStartTime=millis();

  for (i=0; i<Positions; i++){
    int position0 = int(verticalAngles[i]*6400/360);
    int position1 = int(sweepAngleOblique*inputRes*i/(360*(Positions-1))+startAngle1);
    MoveAbsolutePosition(position0, position1);
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

void TakePicsInPlace(){
    //Taking pictures continuosly in mid position.
    //picsInPlaceDelay is the trigger-to-trigger period (ms), gated the same way
    //as the sweep so M4 can run right up to the camera's max FPS
    //(minTimeBetweenFrames) for autofocus, instead of adding a fixed trailing
    //delay on top of the trigger pulse.
    while ((millis()-lastFrameTime)<picsInPlaceDelay){

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
     setupMotorsFast();
    break;
    case 1:
     mode = 1;
     Serial.println("Mode 1: Collection ON, continous");          
     setupMotorsFast();
    break;
    case 2:
      mode = 2;
      Serial.println("Mode 2: Slow and Steady");
      setupMotorsSlow();
    break;
    case 3:
      mode = 3;
      Serial.println("Mode 3: Collection ON Line by line");
      setupMotorsFast();
    break;
    case 4:
     mode = 4;
     Serial.println("Mode 4: Focus adjustment");          
     setupMotorsFast();
    break;
  }
 }

 void calculateMotorAngles () {
  Serial.println("calculating angles");
  for (i=0; i<Positions; i++){
    theta_deg = (sweepAngleVertical/2)-i*sweepAngleVertical/(Positions-1);
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
		return false;
    } else {
		//Serial.println("Move Done");
		return true;
	}


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

