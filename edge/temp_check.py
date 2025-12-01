import time

TEMP_FILE = "/sys/class/thermal/thermal_zone0/temp"
SLEEP_SECS = 2.0  # how often to sample

def get_cpu_temp_c():
    with open(TEMP_FILE, "r") as f:
        return float(f.read().strip()) / 1000.0  # convert millidegC → degC

def main():
    min_t = None
    max_t = None

    print("Starting temperature monitor (Ctrl+C to stop)...")
    try:
        while True:
            t = get_cpu_temp_c()

            if min_t is None or t < min_t:
                min_t = t
            if max_t is None or t > max_t:
                max_t = t

            now = time.strftime("%Y-%m-%d %H:%M:%S")
            print(f"{now}  Temp: {t:.1f} C  (min: {min_t:.1f} C, max: {max_t:.1f} C)", flush=True)

            time.sleep(SLEEP_SECS)

    except KeyboardInterrupt:
        print("\nStopped.")
        if min_t is not None and max_t is not None:
            print(f"Final range: {min_t:.1f} C  →  {max_t:.1f} C")

if __name__ == "__main__":
    main()
