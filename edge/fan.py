from gpiozero import PWMOutputDevice
import time

FAN_PIN12 = 12
FAN_PIN17 = 17

TEMP_MIN = 25.0  # C: below this, fan goes off
TEMP_MAX = 75.0  # C: above this, fan at full speed
SLEEP_TIME = 2.0 # seconds

fan = PWMOutputDevice(FAN_PIN12, frequency=5000)

def get_cpu_temp():
    with open("/sys/class/thermal/thermal_zone0/temp", "r") as f:
        temp_str = f.readline().strip()
    return float(temp_str) / 1000.0

def temp_to_duty(temp):
    if temp <= TEMP_MIN:
        return 0
    elif temp >= TEMP_MAX:
        return 1
    else:
        # linear mapping (as in your screenshot)
        return 1 - ((temp - TEMP_MIN) / (TEMP_MAX - TEMP_MIN))

try:
    while True:
        temp = get_cpu_temp()
        duty = temp_to_duty(temp)
        fan.value = duty
        print(f"CPU: {temp:.1f} C | Duty: {(1-duty)*100:.0f}%")
        time.sleep(SLEEP_TIME)

except KeyboardInterrupt:
    print("Exiting...")

finally:
    pwm.ChangeDutyCycle(0) 
    GPIO.output(FAN_PIN, GPIO.LOW) 
    pwm.stop() 
    GPIO.cleanup()
