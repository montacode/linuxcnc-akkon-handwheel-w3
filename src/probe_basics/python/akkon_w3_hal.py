#!/usr/bin/env python3
"""
AKKON W3 HAL Integration module for LinuxCNC.
Handles HAL pins, handwheel events, and GUI tool selector interaction.
"""

import sys
import os
import time
import signal
import atexit
import threading

# Script-Verzeichnis zum Python-Suchpfad hinzufuegen
script_dir = os.path.dirname(os.path.abspath(__file__))
if script_dir not in sys.path:
    sys.path.insert(0, script_dir)

import hal
import linuxcnc

from qtpy.QtCore import QCoreApplication, Qt
from qtpy.QtGui import QKeyEvent

from handwheel_driver import HandWheelDriver
from gui_manager import GUIManager


class AkkonHALController:
    """Zentrale Steuerungsklasse fuer die HAL-Integration des Akkon W3 Handrads."""

    def __init__(self, port="/dev/ttyUSB0"):
        self.port = port
        self.state = 0
        self.current_speed = 0.0
        self.jog_counters = {'x': 0, 'y': 0, 'z': 0, 'a': 0}

        # 1. LinuxCNC CNC Command & Status Instanzen
        self.cnc_cmd = linuxcnc.command()
        self.cnc_stat = linuxcnc.stat()

        # 2. GUI Manager
        self.gui = GUIManager()

        # 3. HAL Komponente initialisieren
        self.h = self._init_hal()

        # 4. Handrad Treiber initialisieren
        self.hw = HandWheelDriver(port=self.port)
        self.hw.OnFeedChanged = self.on_feed_changed
        self.hw.OnSpeedChanged = self.on_speed_changed
        self.hw.OnKeyDown = self.on_key_down
        self.hw.OnCursorKeyChanged = self.on_cursor_changed

        # 5. Key Handler Mapping fuer saubere Erweiterbarkeit
        self.key_handlers = {
            self.hw.KEY_STOP: self._handle_stop,
            self.hw.KEY_RUN: self._handle_run,
            self.hw.KEY_SPINDLE_ON_OFF: self._handle_spindle,
            self.hw.KEY_W0X: self._handle_w0x,
            self.hw.KEY_JOG: self._handle_jog_mode,
            self.hw.KEY_TOOL: self._handle_tool_dialog,
            self.hw.KEY_ESC: self._handle_esc,
        }

    def _init_hal(self):
        """Erstellt und registriert die HAL-Pins."""
        try:
            h = hal.component("akkon_w3")
        except hal.error:
            time.sleep(0.1)
            try:
                h = hal.component("akkon_w3")
            except hal.error as e_final:
                print(f"[SCHWERWIEGENDER FEHLER] HAL-Registrierung fehlgeschlagen: {e_final}", file=sys.stderr)
                sys.exit(1)

        # Feed & Speed Output Pins
        h.newpin("feed-counts", hal.HAL_S32, hal.HAL_OUT)
        h.newpin("speed-counts", hal.HAL_S32, hal.HAL_OUT)
        h.newpin("jog-increment", hal.HAL_FLOAT, hal.HAL_OUT)

        # Achsen-Pins
        for axis in ['x', 'y', 'z', 'a']:
            h.newpin(f"jog-{axis}-counts", hal.HAL_S32, hal.HAL_OUT)
            h.newpin(f"jog-{axis}-enable", hal.HAL_BIT, hal.HAL_OUT)
            h.newpin(f"jog-{axis}-wheel-active", hal.HAL_BIT, hal.HAL_OUT)
            h.newpin(f"jog-{axis}-vel-mode", hal.HAL_BIT, hal.HAL_OUT)

        # Homing & Trigger Pins
        h.newpin("ref-all-trigger", hal.HAL_BIT, hal.HAL_OUT)
        h.newpin("unhome-all-trigger", hal.HAL_BIT, hal.HAL_OUT)

        # Spindel & Zyklus Trigger
        h.newpin("spindle-on-in", hal.HAL_BIT, hal.HAL_IN)
        h.newpin("spindle-start-trigger", hal.HAL_BIT, hal.HAL_OUT)
        h.newpin("spindle-stop-trigger", hal.HAL_BIT, hal.HAL_OUT)
        h.newpin("cycle-start-trigger", hal.HAL_BIT, hal.HAL_OUT)
        h.newpin("cycle-pause-trigger", hal.HAL_BIT, hal.HAL_OUT)
        h.newpin("cycle-stop-trigger", hal.HAL_BIT, hal.HAL_OUT)

        # Programmstatus-Pins (Input)
        h.newpin("program-is-running", hal.HAL_BIT, hal.HAL_IN)
        h.newpin("program-is-paused", hal.HAL_BIT, hal.HAL_IN)
        h.newpin("program-is-idle", hal.HAL_BIT, hal.HAL_IN)

        h['jog-increment'] = 0.0
        h.ready()
        return h

    def start(self):
        """Startet den Handradtreiber und den Event Loop."""
        if self.hw.connect():
            print("[INFO] Handradtreiber erfolgreich gestartet.")

    def trigger_hal_pulse(self, pin_name, duration=0.1):
        """Setzt einen HAL-Pin unblockierend fuer die angegebene Zeit auf True."""
        self.h[pin_name] = True
        threading.Timer(duration, lambda: self.h.__setitem__(pin_name, False)).start()

    # --- CALLBACKS FUER DAS HANDRAD ---

    def on_feed_changed(self, sender, value):
        self.h['feed-counts'] = int(value / 5.12)

    def on_speed_changed(self, sender, value):
        self.h['speed-counts'] = int(value / 5.12)

    def on_cursor_changed(self, sender, cursor_keys):
        """Verarbeitet Richtungs- und Achsentasten sowie Navigations-Events im Dialog."""
        SIGNAL_POS_EDGE = 1

        # 1. Falls Werkzeugdialog offen ist -> Navigation
        if self.gui.is_dialog_open:
            if cursor_keys[2].TriggerState == SIGNAL_POS_EDGE:
                self.gui.move_up()
            elif cursor_keys[3].TriggerState == SIGNAL_POS_EDGE:
                self.gui.move_down()
            return

        # 2. Regulaerer Jog-Betrieb
        try:
            feed = self.h['feed-counts']
        except Exception:
            feed = 0

        factor = 1 if feed >= 25 else 0
        axes_list = ['x', 'y', 'z', 'a']

        for i, axis in enumerate(axes_list):
            pos_idx = i * 2
            neg_idx = i * 2 + 1

            state_pos = cursor_keys[pos_idx].TriggerState
            state_neg = cursor_keys[neg_idx].TriggerState

            if state_pos in [1, 2]:
                self.jog_counters[axis] += factor
                self.h[f'jog-{axis}-enable'] = True
                self.h[f'jog-{axis}-wheel-active'] = True
                self.h[f'jog-{axis}-counts'] = self.jog_counters[axis]
            elif state_neg in [1, 2]:
                self.jog_counters[axis] -= factor
                self.h[f'jog-{axis}-enable'] = True
                self.h[f'jog-{axis}-wheel-active'] = True
                self.h[f'jog-{axis}-counts'] = self.jog_counters[axis]
            else:
                self.h[f'jog-{axis}-enable'] = False
                self.h[f'jog-{axis}-wheel-active'] = False

    def on_key_down(self, sender, key):
        """Haupt-Tastenevent-Verteiler."""
        print(f"[Handrad] Taste gedrueckt: {key}")

        # 1. DIALOG-STEUERUNG
        if self.gui.is_dialog_open:
            if key == sender.KEY_ENTER:
                print("[GUI] Enter gedrueckt - Versuche Werkzeug zu lesen...")
                self.gui.get_selected_tool_async(self.ausfuehren_werkzeugwechsel)
                return
            elif key == sender.KEY_ESC:
                print("[GUI] Escape gedrueckt - Schliesse Werkzeugdialog...")
                self.gui.close_dialog()
                return

        # 2. WERKZEUGWECHSEL BESTAETIGEN (Wenn LinuxCNC im Tool-Change Pause ist)
        elif key == sender.KEY_ENTER:
            self._handle_enter_confirm()
            return

        # 3. NORMALE TASTENFUNKTIONEN VIA MAP
        handler = self.key_handlers.get(key)
        if handler:
            handler()

    # --- HANDLER-METHODEN FUER EINZELNE TASTEN ---

    def _handle_stop(self):
        print("[Handrad] Stop gedrueckt")
        self.trigger_hal_pulse("cycle-stop-trigger")

    def _handle_run(self):
        print("[Handrad] Run gedrueckt")
        if self.h["program-is-idle"]:
            self.cnc_cmd.mode(linuxcnc.MODE_AUTO)
            self.cnc_cmd.auto(linuxcnc.AUTO_RUN, 1)
            print("[LinuxCNC] Cycle Start ausgeloest")
        elif self.h["program-is-paused"]:
            self.cnc_cmd.auto(linuxcnc.AUTO_RESUME, 1)
            print("[LinuxCNC] Programm fortgesetzt (Resume)")
        else:
            self.cnc_cmd.auto(linuxcnc.AUTO_PAUSE, 1)
            print("[LinuxCNC] Cycle Pause ausgeloest")

    def _handle_spindle(self):
        if self.h["spindle-on-in"]:
            self.hw.set_led(self.hw.LED_SPINDLE, False)
            self.trigger_hal_pulse("spindle-stop-trigger")
        else:
            self.hw.set_led(self.hw.LED_SPINDLE, True)
            self.trigger_hal_pulse("spindle-start-trigger")

    def _handle_w0x(self):
        """Behandelt die W0X / Ref-Taste des Handrads."""
        if self.hw.FNbtn_pressed():
            self.cnc_stat.poll()

            if self.cnc_stat.interp_state == linuxcnc.INTERP_IDLE:
                print("[Handrad] Schalte in Joint-Modus fuer Referenzfahrt...")
                try:
                    self.cnc_cmd.mode(linuxcnc.MODE_MANUAL)
                    self.cnc_cmd.teleop_enable(False)
                    self.cnc_cmd.wait_complete(0.2)

                    print("[Handrad] Starte Referenzfahrt (REF ALL / RE-HOME)...")
                    self.trigger_hal_pulse("ref-all-trigger")
                except Exception as e:
                    print(f"[FEHLER] Umschalten in Joint-Modus fehlgeschlagen: {e}")
            else:
                print("[WARNUNG] Referenzfahrt ignoriert: Maschine ist nicht im Leerlauf (IDLE).")
        else:
            print("[Handrad] Setze X-Werkstuecknullpunkt (G10 L20 P0 X0)...")
            try:
                self.cnc_stat.poll()
                if self.cnc_stat.interp_state == linuxcnc.INTERP_IDLE:
                    self.cnc_cmd.mode(linuxcnc.MODE_MDI)
                    self.cnc_cmd.wait_complete(0.2)
                    self.cnc_cmd.mdi("G10 L20 P0 X0")
                    print("[Handrad] X-Nullpunkt erfolgreich gesetzt.")
                else:
                    print("[WARNUNG] Nullpunkt setzen ignoriert: Interpreter ist nicht IDLE.")
            except Exception as e:
                print(f"[FEHLER] Nullpunkt setzen fehlgeschlagen: {e}")

    def _handle_jog_mode(self):
        self.state = (self.state + 1) % 4
        modes = [self.hw.MM_CONTINUOUS, self.hw.MM_JOG1, self.hw.MM_JOG2, self.hw.MM_JOG3]
        speeds = [0.0, 0.01, 0.1, 0.5]
        labels = ["Kontinuierlich", "Schritt 0.01", "Schritt 0.1", "Schritt 0.5"]

        self.current_speed = speeds[self.state]
        self.h['jog-increment'] = self.current_speed
        self.hw.SetMoveMode(modes[self.state])
        print(f"[Handrad] Modus gewechselt: {labels[self.state]}")

    def _handle_tool_dialog(self):
        print("[Handrad] Tool pressed -> Oeffne/Schliesse Dialog")
        self.gui.zeige_werkzeug_dialog()

    def _handle_esc(self):
        """Behandelt die ESC-Taste des Handrads."""
        print("[Handrad] ESC gedrueckt -> Schliesse Dialoge / Breche ab...")
        self.close_open_dialogs()
        try:
            self.cnc_cmd.abort()
        except Exception as e:
            print(f"[FEHLER] Abort fehlgeschlagen: {e}")

    def _handle_enter_confirm(self):
        try:
            self.cnc_stat.poll()
            if self.cnc_stat.interp_state in [linuxcnc.INTERP_PAUSED, 8]:
                print("[Handrad] ENTER gedrueckt -> Sende Tool-Changed Signal (OK) an LinuxCNC...")
                hal.set_p("halui.tool.changed", "1")
                threading.Timer(0.1, lambda: hal.set_p("halui.tool.changed", "0")).start()
            else:
                print("[Handrad] ENTER gedrueckt (Keine Werkzeugwechsel-Pause aktiv).")
        except Exception as e:
            print(f"[FEHLER] Bestaetigung fehlgeschlagen: {e}")

    # --- LINUXCNC ACTIONS ---

    def ausfuehren_werkzeugwechsel(self, tool_number):
        """Startet die MDI Werkzeugwechsel-Routine fuer Probe Basic."""
        if tool_number is None:
            print("[FEHLER] Kein Werkzeug angegeben!")
            return

        try:
            self.cnc_stat.poll()
            if self.cnc_stat.estop or not self.cnc_stat.enabled:
                print("[FEHLER] Werkzeugwechsel nicht moeglich: Maschine aus oder ESTOP!")
                return

            if self.cnc_stat.interp_state != linuxcnc.INTERP_IDLE:
                print("[FEHLER] Interpreter ist nicht IDLE! Breche alte Aktionen ab...")
                self.cnc_cmd.abort()
                self.cnc_cmd.wait_complete(0.2)

            tool_nr = int(tool_number)
            print(f"[LinuxCNC] Starte Werkzeugwechsel-Routine auf T{tool_nr}...")

            self.cnc_cmd.mode(linuxcnc.MODE_MDI)
            self.cnc_cmd.wait_complete(0.1)

            cmd_m6 = f"T{tool_nr} M6"
            print(f"[LinuxCNC] Sende MDI: {cmd_m6}")
            self.cnc_cmd.mdi(cmd_m6)

        except Exception as e:
            print(f"[FEHLER] Werkzeugwechsel fehlgeschlagen: {e}")
 
    def close_open_dialogs(self):
        """Schliesst Popups, Error-Notifications und Dialoge in Probe Basic/QtPyVCP."""
        # 1. QtPyVCP Notifications & DialogManager direkt im Speicher schliessen
        try:
            from qtpyvcp.widgets.dialogs import DialogManager
            if hasattr(DialogManager, 'close_all'):
                DialogManager.close_all()
        except Exception as e:
            print(f"[DEBUG] DialogManager-Close: {e}")

        try:
            from qtpyvcp.utilities.notifications import NotificationManager
            if hasattr(NotificationManager, 'clear_all'):
                NotificationManager.clear_all()
            elif hasattr(NotificationManager, 'close_all'):
                NotificationManager.close_all()
        except Exception as e:
            print(f"[DEBUG] NotificationManager-Close: {e}")

        # 2. Fallback: Systemweites ESC-Event ueber xdotool an die aktive LinuxCNC GUI senden
        try:
            import subprocess
            subprocess.Popen(["xdotool", "key", "Escape"])
            print("[Handrad] ESC via xdotool an System/Probe Basic gesendet.")
        except Exception:
            # Falls xdotool nicht installiert ist, Fallback auf xte
            try:
                import subprocess
                subprocess.Popen(["xte", "key Escape"])
            except Exception as e:
                print(f"[FEHLER] xdotool/xte nicht verfuegbar: {e}")

        # 3. Fallback: Eigener Werkzeug-Dialog
        if hasattr(self, 'gui') and self.gui.is_dialog_open:
            self.gui.close_dialog() 
    
   
    def cleanup(self):
        """Bereinigt Ressourcen beim Beenden."""
        print("\n[INFO] Speicher und Schnittstellen werden bereinigt...")
        try:
            if hasattr(self, 'hw') and self.hw:
                self.hw.disconnect()
        except Exception as e:
            print(f"Fehler beim Trennen des Handrads: {e}")

        try:
            if hasattr(self, 'h') and self.h:
                self.h.exit()
                print("[INFO] HAL-Komponente erfolgreich entladen.")
        except Exception:
            pass


# --- HAUPT-SKRIPT EXECUTION ---

if __name__ == "__main__":
    controller = AkkonHALController(port='/dev/ttyUSB0')

    def signal_handler(signum, frame):
        print(f"\n[INFO] Signal {signum} empfangen. Beende Anwendung...")
        controller.cleanup()
        sys.exit(0)

    # Signal-Handler registrieren
    signal.signal(signal.SIGHUP, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    atexit.register(controller.cleanup)

    # Handrad starten
    controller.start()

    print("[INFO] Skript aktiv. Starte GUI-Schleife.")
    try:
        controller.gui.mainloop()
    except Exception as e:
        print(f"\n[FEHLER] Unerwarteter Absturz: {e}")
    finally:
        controller.cleanup()
        print("[INFO] Anwendung sauber beendet.")
        sys.exit(0)
