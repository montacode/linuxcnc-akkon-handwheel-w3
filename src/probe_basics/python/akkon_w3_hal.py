#!/usr/bin/env python3
"""
LinuxCNC interface for supporting AKKON Handwheel W3

Create IO interface to form AKKON Handwheel to LinuxCNC and
send commands

Tested with LinuxCNC and probe-basic interface

for installation, please read readme.txt
installation steps
copy akkon_w3_hal.py to /home/geri/linuxcnc/configs/probe_basic/python/
copy handwheel_driver.py to /home/geri/linuxcnc/configs/probe_basic/python/
copy custom.hal to /home/geri/linuxcnc/configs/probe_basic/
copy postgui.hal to /home/geri/linuxcnc/configs/probe_basic/

"""

__version__ = "1.0.0"
__author__ = "Geri"
__status__ = "Development"  # z. B. Development, Prototype, Production

import hal
import time
import os
import sys
import linuxcnc 
from handwheel_driver import HandWheelDriver
import atexit
import signal
import threading

# Globales Event fuer sauberes Beenden definieren
shutdown_event = threading.Event()

# Komponente direkt und ohne Umwege fuer LinuxCNC twopass registrieren
try:
    h = hal.component("akkon_w3")
except hal.error as e:
    time.sleep(0.1)
    try:
        h = hal.component("akkon_w3")
    except hal.error as e_final:
        print(f"[SCHWERWIEGENDER FEHLER] HAL-Registrierung fehlgeschlagen: {e_final}", file=sys.stderr)
        sys.exit(1)

# Pins definieren
h.newpin("feed-counts", hal.HAL_S32, hal.HAL_OUT)
h.newpin("speed-counts", hal.HAL_S32, hal.HAL_OUT)
h.newpin("jog-increment", hal.HAL_FLOAT, hal.HAL_OUT)

axes = ['x', 'y', 'z', 'a']
for axis in axes:
    h.newpin(f"jog-{axis}-counts", hal.HAL_S32, hal.HAL_OUT)
    h.newpin(f"jog-{axis}-enable", hal.HAL_BIT, hal.HAL_OUT)
    h.newpin(f"jog-{axis}-wheel-active", hal.HAL_BIT, hal.HAL_OUT)
    h.newpin(f"jog-{axis}-vel-mode", hal.HAL_BIT, hal.HAL_OUT)
    
h.newpin("ref-all-trigger", hal.HAL_BIT, hal.HAL_OUT)    
h.newpin("spindle-on-in", hal.HAL_BIT, hal.HAL_IN)
h.newpin("spindle-start-trigger", hal.HAL_BIT, hal.HAL_OUT)
h.newpin("spindle-stop-trigger", hal.HAL_BIT, hal.HAL_OUT) 
h.newpin("cycle-start-trigger", hal.HAL_BIT, hal.HAL_OUT)
h.newpin("cycle-pause-trigger", hal.HAL_BIT, hal.HAL_OUT)
h.newpin("cycle-stop-trigger", hal.HAL_BIT, hal.HAL_OUT)
h.newpin("program-is-running", hal.HAL_BIT, hal.HAL_IN)
h.newpin("program-is-paused", hal.HAL_BIT, hal.HAL_IN)
h.newpin("program-is-idle", hal.HAL_BIT, hal.HAL_IN)

h.ready()

# Initialwerte
h['jog-increment'] = 0.0
cnc_cmd = linuxcnc.command()

def on_feed_changed(sender, value):
    h['feed-counts'] = int(value / 5.12)

def on_speed_changed(sender, value):
    h['speed-counts'] = int(value / 5.12)

def trigger_cycle_start():
    cnc_cmd.mode(linuxcnc.MODE_AUTO)
    cnc_cmd.auto(linuxcnc.AUTO_RUN, 1)
    print("Cycle Start ausgeloest")

def trigger_cycle_resume():
    cnc_cmd.auto(linuxcnc.AUTO_RESUME, 1)
    print("Programm fortgesetzt (Resume)")

def trigger_cycle_stop():
    cnc_cmd.abort()
    print("Programm gestoppt")
    
def trigger_cycle_pause():
    cnc_cmd.auto(linuxcnc.AUTO_PAUSE, 1)
    print("Cycle Pause ausgeloest")    

def on_key_down(s, key):
    print("Taste ", key)
    if key == s.KEY_STOP:
        print("Stop pressed")
        h["cycle-stop-trigger"] = True
        h["cycle-stop-trigger"] = False
    elif key == s.KEY_RUN:
        print("Run pressed")
        if h["program-is-idle"]:
            trigger_cycle_start()
        elif h["program-is-paused"]:
            trigger_cycle_resume()
        else: 
            trigger_cycle_pause() 
    elif key == s.KEY_SPINDLE_ON_OFF:                     
        if h["spindle-on-in"]:
            hw.set_led(hw.LED_SPINDLE, False)        
            h["spindle-stop-trigger"] = True
            time.sleep(0.1)
            h["spindle-stop-trigger"] = False
            print("Spindle off")
        else:
            hw.set_led(hw.LED_SPINDLE, True)        
            h["spindle-start-trigger"] = True
            time.sleep(0.1)
            h["spindle-start-trigger"] = False  
            print("Spindle on")          
            
    elif key == s.KEY_W0X:
        if hw.FNbtn_pressed():
            print("Sicherheits-Referenzierung gestartet...")
            h["ref-all-trigger"] = True
            time.sleep(0.1)
            h["ref-all-trigger"] = False
            print("REFALL-Prozess wurde an LinuxCNC uebergeben.")           
        else:
            print("Keine Funktionstaste: Setze X-Werkstuecknullpunkt...")
            cnc_cmd.mode(linuxcnc.MODE_MDI)
            cnc_cmd.wait_complete()
            cnc_cmd.mdi("G10 L20 P0 X0 Y0 Z0 B0 C0")
            cnc_cmd.wait_complete()
            cnc_cmd.mode(linuxcnc.MODE_MANUAL)
            cnc_cmd.wait_complete()
            print("X-Nullpunkt gesetzt.")        
     
    elif key == s.KEY_JOG:
        global state, current_speed
        state = (state + 1) % 4
        modes = [s.MM_CONTINUOUS, s.MM_JOG1, s.MM_JOG2, s.MM_JOG3]
        speeds = [0.0, 0.01, 0.1, 0.5]
        labels = ["Kontinuierlich", "Schritt 0.01", "Schritt 0.1", "Schritt 0.5"]
        
        current_speed = speeds[state]
        h['jog-increment'] = current_speed
        hw.SetMoveMode(modes[state])
        print(f"Modus: {labels[state]}")

    elif key == s.KEY_ENTER:
        print("Enter presssed")
    elif key == s.KEY_ESC:
        print("ESC pressed")

# Statuskonstanten
SIGNAL_POS_EDGE, SIGNAL_HI = 1, 2
state = 0
continuous_mode = True 
last_time = time.time()

# Globale Variable fuer den Takt
jog_counters = {'x': 0, 'y': 0, 'z': 0, 'a': 0}

def on_cursor_changed(s, cursor_keys):
    global h, jog_counters
    
    try:
        feed = h['feed-counts']
    except:
        feed = 0
    
    factor = 1 if feed >= 25 else 0
    axes = ['x', 'y', 'z', 'a']
    
    for i, axis in enumerate(axes):
        pos_idx = i * 2
        neg_idx = i * 2 + 1
        
        state_pos = cursor_keys[pos_idx].TriggerState
        state_neg = cursor_keys[neg_idx].TriggerState
        
        if state_pos in [1, 2]:
            jog_counters[axis] += factor
            h[f'jog-{axis}-enable'] = True
            h[f'jog-{axis}-wheel-active'] = True
            h[f'jog-{axis}-counts'] = jog_counters[axis]
        elif state_neg in [1, 2]:
            jog_counters[axis] -= factor
            h[f'jog-{axis}-enable'] = True
            h[f'jog-{axis}-wheel-active'] = True
            h[f'jog-{axis}-counts'] = jog_counters[axis]
        else:
            h[f'jog-{axis}-enable'] = False
            h[f'jog-{axis}-wheel-active'] = False
            
    print(f"[Jog] Poti={feed} | X={jog_counters['x']} Y={jog_counters['y']} Z={jog_counters['z']} A={jog_counters['a']}")

# Verbindung aufbauen
hw = HandWheelDriver(port='/dev/ttyUSB0')
hw.OnFeedChanged = on_feed_changed
hw.OnSpeedChanged = on_speed_changed
hw.OnKeyDown = on_key_down
hw.OnCursorKeyChanged = on_cursor_changed
hw.connect()

def sauberes_beenden():
    """Wird beim Beenden des Skripts aufgerufen, um Ressourcen freizugeben."""
    print("\n[INFO] Speicher und Schnittstellen werden bereinigt...")
    try:
        if 'hw' in globals() and hasattr(hw, 'disconnect'):
            hw.disconnect()
    except Exception as e:
        print(f"Fehler beim Trennen des Handrads: {e}")

    try:
        if 'h' in globals():
            h.exit()
            print("[INFO] HAL-Komponente erfolgreich entladen.")
    except Exception as e:
        print(f"Fehler beim Beenden der HAL-Komponente: {e}")

def signal_handler_beenden(signum, frame):
    """Setzt das Event, damit die Hauptschleife kontrolliert beendet wird."""
    print(f"\n[INFO] Signal {signum} empfangen. Beende Hauptschleife...")
    shutdown_event.set()

# Registriere Signale für SIGINT (Strg+C), SIGHUP und SIGTERM
signal.signal(signal.SIGINT, signal_handler_beenden)
signal.signal(signal.SIGHUP, signal_handler_beenden)
signal.signal(signal.SIGTERM, signal_handler_beenden)
atexit.register(sauberes_beenden)

print("[INFO] Skript aktiv. Hauptschleife gestartet.")
try:
    # Unterbrechbarer Sleep, reagiert sofort auf Strg+C
    while not shutdown_event.is_set():
        shutdown_event.wait(timeout=0.2)
except KeyboardInterrupt:
    print("\n[INFO] KeyboardInterrupt gefangen.")
finally:
    sauberes_beenden()
    print("[INFO] Anwendung sauber beendet. Auf Wiedersehen.")
    sys.exit(0)
