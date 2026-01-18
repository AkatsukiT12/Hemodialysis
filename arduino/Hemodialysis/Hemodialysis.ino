// ==========================================
// HEMODIALYSIS MACHINE PROTOTYPE WITH CV INTEGRATION
// Sensors: TCS3200 Color (D12/D13/A1/A3), LDR Bubble (A2), DS18B20 Temp (D2)
// CV Integration: Receives state from Python (0=Normal, 1=Possible, 2=Alarm, 3=Missing)
// Actuators: 2 Pumps via H-Bridge, Buzzer, LED, LCD Display
// Developer: Akatsuki Team (Memory optimized)
// ==========================================

#include <Wire.h>
#include <LiquidCrystal_I2C.h>
#include <OneWire.h>
#include <DallasTemperature.h>

// ===== LCD SETUP =====
LiquidCrystal_I2C lcd(0x27, 16, 2);

// ===== DS18B20 SETUP =====
#define ONE_WIRE_BUS 2
OneWire oneWire(ONE_WIRE_BUS);
DallasTemperature sensors(&oneWire);

// ===== PIN DEFINITIONS =====
#define S0 12
#define S1 13
#define S2 A1
#define S3 A3
#define COLOR_OUT 11
#define LDR_PIN A2
#define UV_LED_PIN 3
#define ENA 4
#define IN1 5
#define IN2 6
#define ENB 7
#define IN3 8
#define IN4 9
#define BUZZER_PIN 10
#define LED_PIN A0

// ===== THRESHOLDS =====
float tempThreshold = 38.0;
int bubbleThreshold = 350;
const unsigned long COLOR_TIMEOUT_US = 200000UL;
const unsigned long MIN_DUR_US = 100UL;
const unsigned long MAX_DUR_US = 30000UL;
int BLOOD_RED_THRESHOLD = 110;

// ===== PUMP SPEEDS =====
int pumpSpeedA = 255;
int pumpSpeedB = 255;

// ===== STATE FLAGS (packed into bits to save memory) =====
struct {
  uint8_t pumpsRunning:1;
  uint8_t pumpARunning:1;
  uint8_t pumpBRunning:1;
  uint8_t bubbleAlarmTriggered:1;
  uint8_t bloodDetected:1;
  uint8_t cvConnected:1;
  uint8_t cvTimeout:1;
  uint8_t leakDetected:1;
  uint8_t bubbleDetected:1;
  uint8_t highTemp:1;
} flags = {0};

// ===== BUBBLE STATE =====
uint8_t bubbleState = 0;

// ===== COLOR SENSOR =====
unsigned long redFreq = 0;
unsigned long greenFreq = 0;
unsigned long blueFreq = 0;
int mappedRed = -1;

// Color sensor RGB values (0-255)
int redVal = 0;
int greenVal = 0;
int blueVal = 0;

// Color sensor calibration ranges
long redMin = 25;
long redMax = 200;
long greenMin = 30;
long greenMax = 220;
long blueMin = 28;
long blueMax = 210;

// Blood detection thresholds
int BLOOD_RED_MIN = 100;
int BLOOD_RED_MAX = 255;
int BLOOD_GREEN_MAX = 100;
int BLOOD_BLUE_MAX = 100;

// ===== BLOOD LEAK DETECTION =====
// Multi-method blood detection for improved accuracy
#define BLOOD_DETECTION_SAMPLES 5
#define BLOOD_CONFIDENCE_THRESHOLD 1  // Need 3 out of 5 detections to confirm

struct BloodDetectionState {
  uint8_t consecutiveDetections;
  uint8_t confidenceCounter;
  unsigned long lastDetectionTime;
  unsigned long firstDetectionTime;
  bool leakConfirmed;
  bool persistentLeak;
} bloodState = {0, 0, 0, 0, false, false};

// ===== CV INTEGRATION =====
unsigned long lastCVMessageTime = 0;
const unsigned long CV_TIMEOUT = 5000;
const unsigned long CV_WARNING_TIME = 2000;
int8_t cvState = -1;

// ===== TIMING =====
const unsigned long HEARTBEAT_INTERVAL = 500;
unsigned long lastHeartbeat = 0;
unsigned long lastLCDUpdate = 0;
const unsigned long LCD_UPDATE_INTERVAL = 1000;
char serialBuffer[32]; // Reduced from String to char array
uint8_t bufferIndex = 0;

// ===== SENSOR VARIABLES =====
float temperatureC = 0;
int ldrValue = 0;

// ===== SETUP =====
void setup() {
  Serial.begin(115200);
  
  lcd.init();
  lcd.backlight();
  sensors.begin();

  pinMode(S0, OUTPUT);
  pinMode(S1, OUTPUT);
  pinMode(S2, OUTPUT);
  pinMode(S3, OUTPUT);
  pinMode(COLOR_OUT, INPUT);
  digitalWrite(S0, HIGH);
  digitalWrite(S1, LOW);

  pinMode(LDR_PIN, INPUT);
  pinMode(UV_LED_PIN, OUTPUT);
  pinMode(BUZZER_PIN, OUTPUT);
  pinMode(LED_PIN, OUTPUT);
  pinMode(ENA, OUTPUT);
  pinMode(IN1, OUTPUT);
  pinMode(IN2, OUTPUT);
  pinMode(ENB, OUTPUT);
  pinMode(IN3, OUTPUT);
  pinMode(IN4, OUTPUT);

  digitalWrite(IN1, LOW);
  digitalWrite(IN2, LOW);
  digitalWrite(IN3, LOW);
  digitalWrite(IN4, LOW);
  analogWrite(ENA, 0);
  analogWrite(ENB, 0);
  digitalWrite(UV_LED_PIN, HIGH);

  lcd.setCursor(0, 0);
  lcd.print(F("Hemodialysis"));
  lcd.setCursor(0, 1);
  lcd.print(F("System Init..."));
  delay(1000);
  
  ldrValue = analogRead(LDR_PIN);
  bubbleState = (ldrValue >= bubbleThreshold) ? 0 : 1;
  
  if (readColorSensor()) {
    Serial.print(F("INFO: Color sensor OK - R:"));
    Serial.print(redVal);
    Serial.print(F(" G:"));
    Serial.print(greenVal);
    Serial.print(F(" B:"));
    Serial.print(blueVal);
    Serial.print(F(" | freq R:"));
    Serial.println(redFreq);
  } else {
    Serial.println(F("WARNING: Color sensor timeout"));
  }
  
  lcd.clear();
  lcd.setCursor(0, 0);
  lcd.print(F("Ready for CV"));
  
  Serial.println(F("ARDUINO_READY"));
  
  lastCVMessageTime = millis();
  lastHeartbeat = millis();
}

// ===== LOOP =====
void loop() {
  unsigned long currentTime = millis();
  
  readCVSerial();
  
  if (currentTime - lastHeartbeat >= HEARTBEAT_INTERVAL) {
    sendHeartbeat();
    lastHeartbeat = currentTime;
  }
  
  if (flags.cvConnected && (currentTime - lastCVMessageTime > CV_TIMEOUT)) {
    flags.cvTimeout = true;
    Serial.println(F("WARNING: CV connection lost!"));
  } else if (currentTime - lastCVMessageTime <= CV_TIMEOUT) {
    flags.cvTimeout = false;
  }
  
  sensors.requestTemperatures();
  temperatureC = sensors.getTempCByIndex(0);

  if (readColorSensor()) {
    detectBloodLeak();  // Enhanced blood detection method
  }
  
  ldrValue = analogRead(LDR_PIN);
  flags.highTemp = (temperatureC > tempThreshold);
  
  // Bubble detection
  switch (bubbleState) {
    case 0:
      if (ldrValue < bubbleThreshold) {
        bubbleState = 1;
        Serial.println(F("INFO: Bubble detected entering sensor"));
      }
      flags.bubbleAlarmTriggered = false;
      break;
    case 1:
      if (ldrValue >= bubbleThreshold) {
        bubbleState = 2;
        flags.bubbleAlarmTriggered = true;
        Serial.println(F("ALARM: Bubble passed through sensor!"));
      }
      break;
  }
  
  flags.bubbleDetected = flags.bubbleAlarmTriggered;
  flags.leakDetected = bloodState.leakConfirmed;

  bool sensorAlarm = flags.leakDetected || flags.bubbleDetected || flags.highTemp;

  // Debug monitor
  Serial.print(F("RGB(freq): R:"));
  Serial.print(redFreq);
  Serial.print(F(" G:"));
  Serial.print(greenFreq);
  Serial.print(F(" B:"));
  Serial.print(blueFreq);
  Serial.print(F(" | RGB(val): R:"));
  Serial.print(redVal);
  Serial.print(F(" G:"));
  Serial.print(greenVal);
  Serial.print(F(" B:"));
  Serial.print(blueVal);
  Serial.print(F(" | Blood:"));
  Serial.print(flags.bloodDetected ? F("YES") : F("NO"));
  Serial.print(F(" | Confidence:"));
  Serial.print(bloodState.confidenceCounter);
  Serial.print(F("/"));
  Serial.print(BLOOD_DETECTION_SAMPLES);
  Serial.print(F(" | LDR:"));
  Serial.print(ldrValue);
  Serial.print(F(" | Temp:"));
  Serial.print(temperatureC);
  Serial.print(F(" | PumpA:"));
  Serial.print(flags.pumpARunning ? F("ON") : F("OFF"));
  Serial.print(F(" | PumpB:"));
  Serial.println(flags.pumpBRunning ? F("ON") : F("OFF"));

  // Control logic
  if (currentTime - lastLCDUpdate >= LCD_UPDATE_INTERVAL) {
    lastLCDUpdate = currentTime;
    updateDisplay(sensorAlarm);
  }
  
  controlSystem(sensorAlarm);

  unsigned long delayStart = millis();
  while (millis() - delayStart < 100) {
    readCVSerial();
    delay(10);
  }
  
  // CV timeout warning
  if (flags.cvConnected && !flags.cvTimeout && (currentTime - lastCVMessageTime > CV_WARNING_TIME)) {
    Serial.print(F("WARNING: No CV data for "));
    Serial.print((currentTime - lastCVMessageTime) / 1000.0);
    Serial.println(F(" seconds"));
  }
}

// ===== COLOR FUNCTIONS =====
int mapColor(long freq, long minFreq, long maxFreq) {
  int intensity = map(freq, minFreq, maxFreq, 255, 0);
  return constrain(intensity, 0, 255);
}

bool readColorSensor() {
  // Read RED
  digitalWrite(S2, LOW);
  digitalWrite(S3, LOW);
  redFreq = pulseIn(COLOR_OUT, LOW);
  
  // Read GREEN
  digitalWrite(S2, HIGH);
  digitalWrite(S3, HIGH);
  greenFreq = pulseIn(COLOR_OUT, LOW);
  
  // Read BLUE
  digitalWrite(S2, LOW);
  digitalWrite(S3, HIGH);
  blueFreq = pulseIn(COLOR_OUT, LOW);
  
  // Map frequencies to RGB values (0-255)
  redVal = mapColor(redFreq, redMin, redMax);
  greenVal = mapColor(greenFreq, greenMin, greenMax);
  blueVal = mapColor(blueFreq, blueMin, blueMax);
  
  // Update mappedRed for display
  mappedRed = redVal;
  
  if (redFreq == 0 && greenFreq == 0 && blueFreq == 0) return false;
  return true;
}

// ===== ENHANCED BLOOD LEAK DETECTION =====
void detectBloodLeak() {
  unsigned long currentTime = millis();
  bool currentDetection = false;
  
  // METHOD 1: RGB Value Analysis (High Red, Low Green/Blue)
  bool redHigh = (redVal >= BLOOD_RED_MIN && redVal <= BLOOD_RED_MAX);
  bool greenLow = (greenVal <= BLOOD_GREEN_MAX);
  bool blueLow = (blueVal <= BLOOD_BLUE_MAX);
  
  // METHOD 2: Red Dominance Check (Frequency-based)
  bool redDominant = false;
  if (redFreq > 0 && greenFreq > 0 && blueFreq > 0) {
    redDominant = (redFreq < greenFreq) && (redFreq < blueFreq);
  }
  
  // METHOD 3: Red/Green Ratio Check
  bool rgRatioCheck = false;
  if (greenVal > 0) {
    float rgRatio = (float)redVal / (float)greenVal;
    rgRatioCheck = (rgRatio > 1.5);  // Red should be at least 1.5x green
  }
  
  // METHOD 4: Red/Blue Ratio Check
  bool rbRatioCheck = false;
  if (blueVal > 0) {
    float rbRatio = (float)redVal / (float)blueVal;
    rbRatioCheck = (rbRatio > 1.5);  // Red should be at least 1.5x blue
  }
  
  // METHOD 5: Combined RGB Pattern Match
  // Blood typically has: Red > 150, Green < 100, Blue < 100
  bool patternMatch = (redVal > 100) && (greenVal < redVal * 0.7) && (blueVal < redVal * 0.7);
  
  // COMBINED DETECTION: Blood detected if multiple methods agree
  uint8_t detectionScore = 0;
  if (redHigh && redDominant) detectionScore++;
  if (greenLow && blueLow) detectionScore++;
  if (rgRatioCheck) detectionScore++;
  if (rbRatioCheck) detectionScore++;
  if (patternMatch) detectionScore++;
  
  // Current detection requires at least 3 out of 5 methods
  currentDetection = (detectionScore >= 3);
  flags.bloodDetected = currentDetection;
  
  // CONFIDENCE-BASED CONFIRMATION
  if (currentDetection) {
    bloodState.consecutiveDetections++;
    bloodState.confidenceCounter++;
    
    if (bloodState.consecutiveDetections == 1) {
      bloodState.firstDetectionTime = currentTime;
    }
    
    bloodState.lastDetectionTime = currentTime;
    
    // Confirm leak if we have enough confident detections
    if (bloodState.confidenceCounter >= BLOOD_CONFIDENCE_THRESHOLD) {
      if (!bloodState.leakConfirmed) {
        bloodState.leakConfirmed = true;
        Serial.println(F(""));
        Serial.println(F("========================================"));
        Serial.println(F("  *** BLOOD LEAK CONFIRMED ***"));
        Serial.println(F("========================================"));
        Serial.print(F("Detection Score: "));
        Serial.print(detectionScore);
        Serial.println(F("/5 methods"));
        Serial.print(F("RGB Values: R:"));
        Serial.print(redVal);
        Serial.print(F(" G:"));
        Serial.print(greenVal);
        Serial.print(F(" B:"));
        Serial.println(blueVal);
        Serial.print(F("RGB Freq: R:"));
        Serial.print(redFreq);
        Serial.print(F(" G:"));
        Serial.print(greenFreq);
        Serial.print(F(" B:"));
        Serial.println(blueFreq);
        Serial.print(F("Consecutive detections: "));
        Serial.println(bloodState.consecutiveDetections);
        
        // Print which methods detected blood
        Serial.println(F("Detection Methods:"));
        if (redHigh && redDominant) Serial.println(F("  ✓ Red Dominance"));
        if (greenLow && blueLow) Serial.println(F("  ✓ Low Green/Blue"));
        if (rgRatioCheck) Serial.println(F("  ✓ R/G Ratio"));
        if (rbRatioCheck) Serial.println(F("  ✓ R/B Ratio"));
        if (patternMatch) Serial.println(F("  ✓ Pattern Match"));
        Serial.println(F("========================================"));
      }
      
      // Check for persistent leak (detected for > 2 seconds)
      if (currentTime - bloodState.firstDetectionTime > 2000) {
        bloodState.persistentLeak = true;
      }
    }
  } else {
    // No detection in this cycle
    bloodState.consecutiveDetections = 0;
    
    // Decay confidence slowly (only if no detection for a while)
    if (currentTime - bloodState.lastDetectionTime > 1000) {
      if (bloodState.confidenceCounter > 0) {
        bloodState.confidenceCounter--;
      }
      
      // Clear confirmed state if confidence drops
      if (bloodState.confidenceCounter < BLOOD_CONFIDENCE_THRESHOLD) {
        if (bloodState.leakConfirmed) {
          Serial.println(F("INFO: Blood leak cleared"));
        }
        bloodState.leakConfirmed = false;
        bloodState.persistentLeak = false;
      }
    }
  }
  
  // Periodic detailed reporting when blood is detected
  if (flags.bloodDetected) {
    static unsigned long lastDetailReport = 0;
    if (currentTime - lastDetailReport > 3000) {
      Serial.println(F("--- Blood Detection Report ---"));
      Serial.print(F("Score: "));
      Serial.print(detectionScore);
      Serial.print(F("/5 | Confidence: "));
      Serial.print(bloodState.confidenceCounter);
      Serial.print(F("/"));
      Serial.println(BLOOD_DETECTION_SAMPLES);
      Serial.print(F("Consecutive: "));
      Serial.print(bloodState.consecutiveDetections);
      Serial.print(F(" | Confirmed: "));
      Serial.print(bloodState.leakConfirmed ? F("YES") : F("NO"));
      Serial.print(F(" | Persistent: "));
      Serial.println(bloodState.persistentLeak ? F("YES") : F("NO"));
      lastDetailReport = currentTime;
    }
  }
}

// ===== CONTROL =====
void controlSystem(bool alarm) {
  if (alarm) {
    stopPumps();
    digitalWrite(BUZZER_PIN, HIGH);
    digitalWrite(LED_PIN, HIGH);
  }
  else if (flags.cvTimeout) {
    runPumps(pumpSpeedA, pumpSpeedB);
    digitalWrite(BUZZER_PIN, HIGH);
    digitalWrite(LED_PIN, HIGH);
  }
  else if (cvState == 2 || cvState == 3) {
    stopPumps();
    digitalWrite(BUZZER_PIN, HIGH);
    digitalWrite(LED_PIN, HIGH);
  }
  else if (cvState == 1) {
    runPumps(pumpSpeedA, pumpSpeedB);
    digitalWrite(BUZZER_PIN, LOW);
    digitalWrite(LED_PIN, HIGH);
  }
  else {
    runPumps(pumpSpeedA, pumpSpeedB);
    digitalWrite(BUZZER_PIN, LOW);
    digitalWrite(LED_PIN, LOW);
  }
}

// ===== DISPLAY =====
void updateDisplay(bool alarm) {
  lcd.clear();
  
  if (alarm) {
    if (flags.leakDetected) {
      lcd.setCursor(0, 0);
      if (bloodState.persistentLeak) {
        lcd.print(F("CRITICAL:LEAK!"));
      } else {
        lcd.print(F("ALARM:BLOOD LEAK"));
      }
      lcd.setCursor(0, 1);
      lcd.print(F("R:"));
      lcd.print(redVal);
      lcd.print(F(" Conf:"));
      lcd.print(bloodState.confidenceCounter);
      lcd.print(F("/"));
      lcd.print(BLOOD_DETECTION_SAMPLES);
    } else if (flags.bubbleDetected) {
      lcd.setCursor(0, 0);
      lcd.print(F("ALARM:AIR BUBBLE"));
      lcd.setCursor(0, 1);
      lcd.print(F("Bubble Passed!"));
    } else if (flags.highTemp) {
      lcd.setCursor(0, 0);
      lcd.print(F("ALARM: HIGH TEMP"));
      lcd.setCursor(0, 1);
      lcd.print(F("T:"));
      lcd.print(temperatureC, 1);
      lcd.print(F("C"));
    }
  }
  else if (flags.cvTimeout) {
    lcd.setCursor(0, 0);
    lcd.print(F("WARN: CV LOST"));
    lcd.setCursor(0, 1);
    lcd.print(F("Check Connection"));
  }
  else if (cvState == 2) {
    lcd.setCursor(0, 0);
    lcd.print(F("ALARM:HEAD DROOP"));
    lcd.setCursor(0, 1);
    lcd.print(F("Patient Alert!"));
  }
  else if (cvState == 3) {
    lcd.setCursor(0, 0);
    lcd.print(F("ALARM: PATIENT"));
    lcd.setCursor(0, 1);
    lcd.print(F("MISSING!"));
  }
  else if (cvState == 1) {
    lcd.setCursor(0, 0);
    lcd.print(F("WARN:Possible"));
    lcd.setCursor(0, 1);
    lcd.print(F("Head Droop"));
  }
  else {
    lcd.setCursor(0, 0);
    if (flags.cvConnected) {
      lcd.print(F("Normal [CV:OK]"));
    } else {
      lcd.print(F("Normal [No CV]"));
    }
    lcd.setCursor(0, 1);
    lcd.print(F("T:"));
    lcd.print(temperatureC, 1);
    lcd.print(F("C R:"));
    lcd.print(redVal);
  }
}

// ===== SERIAL =====
void readCVSerial() {
  while (Serial.available() > 0) {
    char c = Serial.read();
    
    if (c == '\n') {
      serialBuffer[bufferIndex] = '\0';
      processCommand();
      bufferIndex = 0;
    } else if (bufferIndex < 31) {
      serialBuffer[bufferIndex++] = c;
    } else {
      bufferIndex = 0;
    }
  }
}

void processCommand() {
  if (strcmp(serialBuffer, "PYTHON_READY") == 0) {
    Serial.println(F("ACK"));
    flags.cvConnected = true;
    lastCVMessageTime = millis();
  }
  else if (strncmp(serialBuffer, "CV:", 3) == 0) {
    cvState = atoi(serialBuffer + 3);
    lastCVMessageTime = millis();
    flags.cvConnected = true;
    Serial.println(F("ACK"));
  }
  else if (strcmp(serialBuffer, "RESET_BUBBLE") == 0) {
    bubbleState = 0;
    flags.bubbleAlarmTriggered = false;
    Serial.println(F("INFO: Bubble alarm reset"));
  }
  else if (strcmp(serialBuffer, "RESET_BLOOD") == 0) {
    // Reset blood detection state
    bloodState.consecutiveDetections = 0;
    bloodState.confidenceCounter = 0;
    bloodState.leakConfirmed = false;
    bloodState.persistentLeak = false;
    flags.bloodDetected = false;
    flags.leakDetected = false;
    Serial.println(F("INFO: Blood leak alarm reset"));
  }
  else if (strncmp(serialBuffer, "SET_THRESHOLD:", 14) == 0) {
    BLOOD_RED_MIN = atoi(serialBuffer + 14);
    Serial.print(F("INFO: Blood red threshold set to: "));
    Serial.println(BLOOD_RED_MIN);
  }
  else if (strncmp(serialBuffer, "SET_BLOOD_THRESHOLDS:", 21) == 0) {
    // Format: SET_BLOOD_THRESHOLDS:redMin,redMax,greenMax,blueMax
    char* token = strtok(serialBuffer + 21, ",");
    if (token) BLOOD_RED_MIN = atoi(token);
    token = strtok(NULL, ",");
    if (token) BLOOD_RED_MAX = atoi(token);
    token = strtok(NULL, ",");
    if (token) BLOOD_GREEN_MAX = atoi(token);
    token = strtok(NULL, ",");
    if (token) BLOOD_BLUE_MAX = atoi(token);
    
    Serial.println(F("INFO: Blood detection thresholds updated"));
    Serial.print(F("Red: "));
    Serial.print(BLOOD_RED_MIN);
    Serial.print(F("-"));
    Serial.print(BLOOD_RED_MAX);
    Serial.print(F(" | Green Max: "));
    Serial.print(BLOOD_GREEN_MAX);
    Serial.print(F(" | Blue Max: "));
    Serial.println(BLOOD_BLUE_MAX);
  }
  else if (strncmp(serialBuffer, "CALIBRATE:", 10) == 0) {
    // Format: CALIBRATE:redMin,redMax,greenMin,greenMax,blueMin,blueMax
    char* token = strtok(serialBuffer + 10, ",");
    if (token) redMin = atol(token);
    token = strtok(NULL, ",");
    if (token) redMax = atol(token);
    token = strtok(NULL, ",");
    if (token) greenMin = atol(token);
    token = strtok(NULL, ",");
    if (token) greenMax = atol(token);
    token = strtok(NULL, ",");
    if (token) blueMin = atol(token);
    token = strtok(NULL, ",");
    if (token) blueMax = atol(token);
    
    Serial.println(F("INFO: Color calibration updated"));
    Serial.print(F("R:"));
    Serial.print(redMin);
    Serial.print(F("-"));
    Serial.print(redMax);
    Serial.print(F(" G:"));
    Serial.print(greenMin);
    Serial.print(F("-"));
    Serial.print(greenMax);
    Serial.print(F(" B:"));
    Serial.print(blueMin);
    Serial.print(F("-"));
    Serial.println(blueMax);
  }
}

void sendHeartbeat() {
  Serial.print(F("STATUS:"));
  Serial.print(cvState);
  Serial.print(F(","));
  Serial.print(temperatureC, 1);
  Serial.print(F(","));
  Serial.print(redFreq);
  Serial.print(F(","));
  Serial.print(redVal);
  Serial.print(F(","));
  Serial.print(ldrValue);
  Serial.print(F(","));
  Serial.print(flags.pumpARunning);
  Serial.print(F(","));
  Serial.print(flags.pumpBRunning);
  Serial.print(F(","));
  Serial.print(bubbleState);
  Serial.print(F(","));
  Serial.print(flags.bloodDetected);
  Serial.print(F(","));
  Serial.print(bloodState.leakConfirmed);
  Serial.print(F(","));
  Serial.println(bloodState.confidenceCounter);
}

// ===== PUMP CONTROL =====
void runPumps(int speedA, int speedB) {
  if (!flags.pumpARunning) {
    Serial.println(F("INFO: Starting Pump A"));
    flags.pumpARunning = true;
  }
  digitalWrite(IN1, HIGH);
  digitalWrite(IN2, LOW);
  analogWrite(ENA, speedA);
  
  if (!flags.pumpBRunning) {
    Serial.println(F("INFO: Starting Pump B"));
    flags.pumpBRunning = true;
  }
  digitalWrite(IN3, HIGH);
  digitalWrite(IN4, LOW);
  analogWrite(ENB, speedB);
  
  flags.pumpsRunning = true;
}

void stopPumps() {
  if (flags.pumpARunning) {
    Serial.println(F("INFO: Stopping Pump A"));
    flags.pumpARunning = false;
  }
  analogWrite(ENA, 0);
  digitalWrite(IN1, LOW);
  digitalWrite(IN2, LOW);
  
  if (flags.pumpBRunning) {
    Serial.println(F("INFO: Stopping Pump B"));
    flags.pumpBRunning = false;
  }
  analogWrite(ENB, 0);
  digitalWrite(IN3, LOW);
  digitalWrite(IN4, LOW);
  
  flags.pumpsRunning = false;
}