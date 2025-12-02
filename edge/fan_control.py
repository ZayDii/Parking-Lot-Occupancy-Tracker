from gpiozero import PWMOutputDevice, DigitalOutputDevice
import time

# --- Pins ---
PWM_PIN = 12      # GPIO12 (physical pin 32) - your working PWM pin
EN_PIN  = 17      # GPIO17 (physical pin 11) - enable/kill pin (any GPIO is fine)

# --- Behavior ---
FREQUENCY = 5000
INVERT_PWM = False   # set True only if your fan behaves backwards
TEMP_MIN = 25.0      # C: below this, fan off
TEMP_MAX = 70.0      # C: above this, fan full
SLEEP_TIME = 2.0     # seconds

# Devices
fan_pwm = PWMOutputDevice(PWM_PIN, frequency=FREQUENCY, initial_value=0.0)
fan_en  = DigitalOutputDevice(EN_PIN, initial_value=False)  # start disabled


def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, float(x)))


def set_speed(x: float):
    """
    x in [0..1]. 0=off, 1=full.
    ENABLE is forced ON when setting speed > 0.
    """
    x = _clamp01(x)
    if x <= 0.0:
        off()
        return

    fan_en.on()
    fan_pwm.value = (1.0 - x) if INVERT_PWM else x


def on():
    set_speed(1.0)


def off():
    # hard off: disable + pwm=0
    fan_pwm.value = 0.0
    fan_en.off()


def close():
    off()
    fan_pwm.close()
    fan_en.close()


def get_cpu_temp():
    with open("/sys/class/thermal/thermal_zone0/temp", "r") as f:
        return float(f.readline().strip()) / 1000.0


def temp_to_speed(temp_c: float) -> float:
    # maps TEMP_MIN..TEMP_MAX -> 0..1
    if temp_c <= TEMP_MIN:
        return 0.0
    if temp_c >= TEMP_MAX:
        return 1.0
    return (temp_c - TEMP_MIN) / (TEMP_MAX - TEMP_MIN)


def run_auto():
    """Your old while-loop mode (only runs if you call it)."""
    try:
        while True:
            t = get_cpu_temp()
            sp = temp_to_speed(t)
            set_speed(sp)
            print(f"CPU: {t:.1f} C | Fan: {sp*100:.0f}%")
            time.sleep(SLEEP_TIME)
    except KeyboardInterrupt:
        pass
    finally:
        close()


if __name__ == "__main__":
    run_auto()
