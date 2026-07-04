// Office Electrical Monitor — representative one-room circuit (Drawing Room)
//
// Hardware behaviour (see README.md):
//   - Potentiometer on A0 sets PWM duty for both fan motors (D8, D9).
//   - Three slide switches drive three LEDs manually at the load side
//     (not MCU pins — models wall switches wired directly to lights).
//
// Serial Monitor (9600 baud): potentiometer raw value and PWM output each tick.

int sensorValue = 0;
int outputValue = 0;

void setup() {
  pinMode(A0, INPUT);
  pinMode(8, OUTPUT);
  pinMode(9, OUTPUT);
  Serial.begin(9600);
}

void loop() {
  sensorValue = analogRead(A0);
  outputValue = map(sensorValue, 0, 1023, 0, 255);

  analogWrite(8, outputValue);
  analogWrite(9, outputValue);

  Serial.print("sensor = ");
  Serial.print(sensorValue);
  Serial.print("     output = ");
  Serial.println(outputValue);

  delay(100);
}
