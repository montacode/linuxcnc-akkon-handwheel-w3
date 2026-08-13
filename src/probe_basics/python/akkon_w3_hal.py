#!/usr/bin/env python3
import hal
import time
import sys
import linuxcnc
import atexit
import signal
from handwheel_driver import HandWheelDriver
from gui_manager import GUIManager

# HAL-Komponente fuer LinuxCNC twopass registrieren
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

# Initialwerte & Instanzen
h['jog-increment'] = 0.0
cnc_cmd = linuxcnc.command()
gui = GUIManager()

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

def ausfuehren_werkzeugwechsel(tool_number):
    """Fuehrt den Werkzeugwechsel per MDI in LinuxCNC aus."""
    try:
        cnc_stat = linuxcnc.stat()
        cnc_stat.poll()

        if cnc_stat.estop:
            print("[FEHLER] Werkzeugwechsel nicht moeglich: ESTOP ist aktiv!")
            return

        if not cnc_stat.enabled:
            print("[FEHLER] Werkzeugwechsel nicht moeglich: Maschine ist OFF!")
            return

        print(f"[LinuxCNC] Starte Werkzeugwechsel auf T{tool_number}...")
        cnc_cmd.mode(linuxcnc.MODE_MDI)
        cnc_cmd.wait_complete(2.0)
        
        command_str = f"M6 T{int(tool_number)} G43"
        print(f"[LinuxCNC] Sende MDI: {command_str}")
        cnc_cmd.mdi(command_str)
        cnc_cmd.wait_complete(5.0)
        
        cnc_cmd.mode(linuxcnc.MODE_MANUAL)
        cnc_cmd.wait_complete(2.0)
        print(f"[LinuxCNC] Werkzeug T{tool_number} erfolgreich gewechselt.")

    except Exception as e:
        print(f"[FEHLER] Werkzeugwechsel fehlgeschlagen: {e}")

def on_key_down(s, key):
    print("Taste ", key)

# --- DIALOG-STEUERUNG (falls Fenster offen ist) ---
    if gui.is_dialog_open:
        if key == s.KEY_ENTER:  # Taste 23
            print("[GUI] Enter gedrueckt - Versuche Werkzeug zu lesen...")

            def on_tool_selected(selected_tool):
                print(f"[DEBUG] Ausgewaehltes Werkzeug: {selected_tool}")
                
                if selected_tool is not None:
                    ausfuehren_werkzeugwechsel(selected_tool)
                else:
                    print("[FEHLER] 'selected_tool' war None! Kein Werkzeug erkannt.")

            # Liest Wert aus, SCHLIESST DAS FENSTER und ruft dann on_tool_selected auf
            gui.get_selected_tool_async(on_tool_selected)
            return   
        # 2. Escape-Taste: Dialog einfach abbrechen & schließen
        elif key == s.KEY_ESC:
            print("[GUI] Escape gedrueckt - Schliesse Werkzeugdialog ohne Auswahl...")
            gui.close_dialog()
            return         
     
    # --- NORMALE TASTENFUNKTIONEN ---
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
        else:
            hw.set_led(hw.LED_SPINDLE, True)        
            h["spindle-start-trigger"] = True
            time.sleep(0.1)
            h["spindle-start-trigger"] = False            
            
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

    elif key == s.KEY_TOOL:
        print("Tool pressed -> Oeffne/Schließe Dialog")
        gui.zeige_werkzeug_dialog()

# Statuskonstanten
SIGNAL_POS_EDGE, SIGNAL_HI = 1, 2
state = 0
jog_counters = {'x': 0, 'y': 0, 'z': 0, 'a': 0}

def on_cursor_changed(s, cursor_keys):
    global h, jog_counters
    
    # --- NAVIGATION IM TOOLDIALOG ---
    if gui.is_dialog_open:
        up_triggered = cursor_keys[2].TriggerState == SIGNAL_POS_EDGE
        down_triggered = cursor_keys[3].TriggerState == SIGNAL_POS_EDGE

        if up_triggered:
            gui.move_up()
        elif down_triggered:
            gui.move_down()
        return

    # --- NORMALE JOG-LOGIK ---
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
    except:
        pass

def signal_handler_beenden(signum, frame):
    print(f"\n[INFO] Signal {signum} empfangen (Terminal geschlossen). Raeume auf...")
    sauberes_beenden()
    sys.exit(0)

signal.signal(signal.SIGHUP, signal_handler_beenden)
signal.signal(signal.SIGTERM, signal_handler_beenden)
atexit.register(sauberes_beenden)

print("[INFO] Skript aktiv. Starte GUI-Schleife.")
try:
    gui.mainloop()
except KeyboardInterrupt:
    print("\n[INFO] Abbruch durch Benutzer (Strg+C).")
except Exception as e:
    print(f"\n[FEHLER] Unerwarteter Absturz: {e}")
finally:
    sauberes_beenden()
    print("[INFO] Anwendung sauber beendet. Auf Wiedersehen.")
    sys.exit(0)
