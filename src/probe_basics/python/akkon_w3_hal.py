#!/usr/bin/env python3
import hal
import time
import sys
import linuxcnc
import atexit
import signal
import threading
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
h.newpin("unhome-all-trigger", hal.HAL_BIT, hal.HAL_OUT)

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
    """Startet den manuellen Werkzeugwechsel inkl. Fahrt, Pause & Vermessung."""
    try:
        cnc_stat = linuxcnc.stat()
        cnc_stat.poll()

        if cnc_stat.estop or not cnc_stat.enabled:
            print("[FEHLER] Werkzeugwechsel nicht moeglich: Maschine aus oder ESTOP!")
            return

        if cnc_stat.interp_state != linuxcnc.INTERP_IDLE:
            print("[FEHLER] Interpreter ist nicht IDLE! Breche alte Aktionen ab...")
            cnc_cmd.abort()
            cnc_cmd.wait_complete(0.2)

        tool_nr = int(tool_number)
        print(f"[LinuxCNC] Starte Werkzeugwechsel-Routine auf T{tool_nr}...")

        # 1. In MDI-Modus wechseln
        cnc_cmd.mode(linuxcnc.MODE_MDI)
        cnc_cmd.wait_complete(0.1)

        # 2. Nur T<nr> M6 senden (OHNE nachfolgendes G43)
        # T<nr> M6 startet die Probe Basic Subroutine:
        # -> Faehrt auf Wechselpos
        # -> Stoppt Spindel
        # -> Öffnet Dialog / Wartet auf OK
        cmd_m6 = f"T{tool_nr} M6"
        print(f"[LinuxCNC] Sende MDI: {cmd_m6}")
        cnc_cmd.mdi(cmd_m6)

        print(f"[LinuxCNC] Wechselbefehl T{tool_nr} gesendet. Bitte am Bildschirm / Dialog bestaetigen!")

    except Exception as e:
        print(f"[FEHLER] Werkzeugwechsel fehlgeschlagen: {e}")    

    


def trigger_hal_pulse(pin_name):
    """Setzt einen HAL-Pin unblockierend fuer 100ms auf True und danach wieder auf False."""
    h[pin_name] = True
    # threading.Timer blockiert weder die GUI noch den Handrad-Loop
    threading.Timer(0.1, lambda: h.__setitem__(pin_name, False)).start()


def on_key_down(s, key):
    print("Taste ", key)

    # --- 1. DIALOG-STEUERUNG (Falls Handrad-Werkzeugdialog offen ist) ---
    if gui.is_dialog_open:
        if key == s.KEY_ENTER:  # Taste 23
            print("[GUI] Enter gedrueckt - Versuche Werkzeug zu lesen...")

            def on_tool_selected(selected_tool):
                print(f"[DEBUG] Ausgewaehltes Werkzeug: {selected_tool}")
                
                if selected_tool is not None:
                    ausfuehren_werkzeugwechsel(selected_tool)
                else:
                    print("[FEHLER] 'selected_tool' war None! Kein Werkzeug erkannt.")

            gui.get_selected_tool_async(on_tool_selected)
            return   
            
        elif key == s.KEY_ESC:
            print("[GUI] Escape gedrueckt - Schliesse Werkzeugdialog ohne Auswahl...")
            gui.close_dialog()
            return         

    # --- 2. WERKZEUGWECHSEL BESTÄTIGEN (Wenn Dialog ZU ist & LinuxCNC auf G8 wartet) ---
    elif key == s.KEY_ENTER:
        try:
            cnc_stat = linuxcnc.stat()
            cnc_stat.poll()
            
            # Pruefen, ob die Maschine paussiert ist / auf Bestaetigung wartet (G8 / INTERP_PAUSED)
            if cnc_stat.interp_state in [linuxcnc.INTERP_PAUSED, 8]:
                print("[Handrad] ENTER gedrueckt -> Sende Tool-Changed Signal (OK) an LinuxCNC...")

                # HAL-Signal kurz auf True setzen und nach 100ms wieder abfallen lassen
                hal.set_p("halui.tool.changed", "1")
                threading.Timer(0.1, lambda: hal.set_p("halui.tool.changed", "0")).start()

            else:
                print("[Handrad] ENTER gedrueckt (Keine Werkzeugwechsel-Pause aktiv).")

        except Exception as e:
            print(f"[FEHLER] Bestaetigung fehlgeschlagen: {e}")
        return

    # --- 3. NORMALE TASTENFUNKTIONEN ---
    if key == s.KEY_STOP:
        print("Stop pressed")
        trigger_hal_pulse("cycle-stop-trigger")

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
            trigger_hal_pulse("spindle-stop-trigger")
        else:
            hw.set_led(hw.LED_SPINDLE, True)        
            trigger_hal_pulse("spindle-start-trigger")            
            
    elif key == s.KEY_W0X:
        if hw.FNbtn_pressed():
            cnc_stat = linuxcnc.stat()
            cnc_stat.poll()
            
            # True wenn alle 3 Haupt-Achsen (X, Y, Z) referenziert sind
            is_homed = all(cnc_stat.homed[:3])

            if not is_homed:
                print("[Handrad] Maschine ist UNHOMED -> Starte Referenzierung (REF ALL)...")
                trigger_hal_pulse("ref-all-trigger")
            else:
                print("[Handrad] Maschine ist HOMED -> Hebe Referenzierung auf (UNHOME ALL)...")
                try:
                    cnc_cmd.teleop_enable(0)
                    for joint_num in range(3):
                        cnc_cmd.unhome(joint_num)
                    print("[Handrad] UNHOME-Befehl erfolgreich an LinuxCNC gesendet.")
                except Exception as e:
                    print(f"[FEHLER] Unhome fehlgeschlagen: {e}")
        else:
            print("Keine Funktionstaste: Setze X-Werkstuecknullpunkt...")
            cnc_cmd.mode(linuxcnc.MODE_MDI)
            cnc_cmd.mdi("G10 L20 P0 X0 Y0 Z0 B0 C0")
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
        print("Tool pressed -> Oeffne/Schliesse Dialog")
        gui.zeige_werkzeug_dialog()



# Statuskonstanten
SIGNAL_POS_EDGE, SIGNAL_HI = 1, 2
state = 0
jog_counters = {'x': 0, 'y': 0, 'z': 0, 'a': 0}

def on_cursor_changed(s, cursor_keys):
    global h, jog_counters
    
    if gui.is_dialog_open:
        up_triggered = cursor_keys[2].TriggerState == SIGNAL_POS_EDGE
        down_triggered = cursor_keys[3].TriggerState == SIGNAL_POS_EDGE

        if up_triggered:
            gui.move_up()
        elif down_triggered:
            gui.move_down()
        return

    try:
        feed = h['feed-counts']
    except:
        feed = 0
    
    factor = 1 if feed >= 25 else 0
    axes_list = ['x', 'y', 'z', 'a']
    
    for i, axis in enumerate(axes_list):
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
