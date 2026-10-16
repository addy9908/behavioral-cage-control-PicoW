from machine import Pin
import time

# --- LAUNCHER & SAFETY SWITCH ---
# GP22 is pulled HIGH by default. Connect to GND to abort boot.
safety_pin = Pin(22, Pin.IN, Pin.PULL_UP)

print("Booting Pico W...")
time.sleep(0.5)

if safety_pin.value() == 0:
    print("====================================================")
    print("🛑 SAFETY SWITCH ACTIVATED (GP22 is Grounded)")
    print("Boot sequence aborted. Dropping to REPL for Thonny.")
    print("====================================================")
else:
    print("No safety jumper detected. Launching Behavior App...")
    import app
