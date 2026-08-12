"""
AKKON W3 Handwheel driver

Establish RS232 serial connection over FTDI FT232 chip, process input
and output from handwheel as well as create related events.
"""

__version__ = "1.0.0"
__author__ = "Geri"
__status__ = "Development"  # z. B. Development, Prototype, Production

import serial
import time
import threading
import struct
import serial.tools.list_ports  # <--- Diese Zeile zwingend ergänzen
from typing import Final

SIGNAL_LO, SIGNAL_POS_EDGE, SIGNAL_HI, SIGNAL_NEG_EDGE = 0, 1, 2, 3


# Passend zu deinem Delphi-Array, index 0 ist ein Dummy-Wert
AHW_ID: Final[int] = 0x01ABCDEF
RUN_KEY_ID :Final[int] = 22 


class CursorKeyStatus:
    def __init__(self):
        self.KeyDownTime = 0
        self.TriggerState = SIGNAL_LO
        self.NextKeyEvent = 0
        self.Intervall = 250  # ms
        self.Changed = False
        self.KeyMask = 0
        
class HandWheelDriver:
    CMD_STATE = 200
    CMD_GET_KEYBUF = 201
    CMD_GET_FIRMWARE_VER = 213
    CMD_GET_COMPILATION = 214
    CMD_GET_HARDWARE_VER = 215
    CMD_GET_DEVICE_NAME = 229
    CMD_GET_AHW_ID = 230
    CMD_SET_LED = 205

    KEY_TOOL = 16
    KEY_W0X = 17
    KEY_RUN = 18
    KEY_STOP = 19
    KEY_SPINDLE_ON_OFF = 20
    KEY_ESC = 21
    KEY_FN = 22
    KEY_ENTER = 23
    KEY_JOG = 15
    KEY_CURSOR_YUP = 3
    KEY_CURSOR_YDn = 9
    

    LED_JOG1 = 3
    LED_JOG2 = 5
    LED_JOG3 = 4
    LED_SPINDLE = 10

    MM_CONTINUOUS = 0
    MM_JOG1 = 1
    MM_JOG2 = 2
    MM_JOG3 = 3

	# KeyMaskW3, für Mapping keys
    KeyMaskW3 = [
        0x00000000, # Index 0 (ungenutzt)
        0x00000000, 0x00000010, 0x00000040, 0x00000080, 0x00001000, 
        0x00000008, 0x00800000, 0x00000001, 0x00100000, 0x00040000, 
        0x00000800, 0x00000002, 0x04000000, 0x02000000, 0x00000100, 
        0x01000000, 0x08000000, 0x00000400, 0x00004000, 0x80000000, 
        0x00020000, 0x00008000, 0x00000004
    ]    
    def __init__(self, port='COM9', baudrate=115200):
        # Basis-Hardware-Verbindung
        self.ser = serial.Serial(port, baudrate, timeout=0.1)
        self.lock = threading.Lock()
        self.running = False
        
        # AHW Parameter-Speicher
        self.mAHWParams = {
            'Keys': 0, 'Feed': 0, 'Speed': 0, 
            'State': 0, 'LedState': 0, 'KeyEventCount': 0
        }
        
        # Tracking-Variablen
        self.last_feed = -1
        self.last_speed = -1
        self.mCurrentInfo = 0
        self.mCursorKeys = 0
        self.mKeyChanged = False
        self.OnKeyDown = None
        
        # 1. Cursor-Status Array (für die 4 Achsen X, Y, Z, A / Pos + Neg)
        # Wir verwenden hier die CursorKeyStatus-Klasse für saubere Attribut-Zugriffe
        self.mCursorKey = [CursorKeyStatus() for _ in range(8)]
        
        # 2. Hardware-Keys Puffer (23 Tasten + 1 Dummy für Index 0)
        self.mHwdKeys = [CursorKeyStatus() for _ in range(24)]
        
        # 3. Konstanten-Mapping für Achsen-Masken (X, Y, Z, A)
        # Entspricht der Struktur in _process_cursor_keys
        self.cKeyMask = [
            (0x0001, 0x0002), # Achse 0 (X): Pos/Neg
            (0x0004, 0x0008), # Achse 1 (Y): Pos/Neg
            (0x0010, 0x0020), # Achse 2 (Z): Pos/Neg
            (0x0040, 0x0080)  # Achse 3 (A): Pos/Neg
        ]
        
        # Callbacks
        self.OnCursorKeyChanged = None
        self.OnFeedChanged = None
        self.OnSpeedChanged = None
        
        self.mFirmwareVersion = ""
        self.mHardwareVersion = ""
        self.mCompilationDate = ""
        self.mDeviceName = ""
        self.mAHWID = ""
        self._move_mode = self.MM_CONTINUOUS
        

    def FNbtn_pressed(self) -> bool:
              
        with self.lock:
            # Holen der Keys aus dem Dictionary
            # Standardwert 0, falls der Key 'Keys' noch nicht existiert
            current_keys = self.mAHWParams.get('Keys', 0)
            
            # Die Bit-Operation wie in Delphi: (Keys AND $8000) <> 0
            return (current_keys & 0x8000) != 0
        

    def SendReceivePacket(self, send_data, receive_len, send_delay, receive_delay):
        """
        Sendet ein Paket und wartet, bis 'receive_len' Bytes empfangen wurden.
        Sammelt Fragmente, falls die Antwort in Stücken ankommt.
        """
        try:
            # 1. Sende-Verzögerung falls nötig
            if send_delay > 0:
                time.sleep(send_delay / 1000.0)
            
            # 2. Puffer leeren und senden
            self.ser.reset_input_buffer()
            self.ser.write(send_data)
            
            # 3. Empfangs-Logik mit Puffer-Sammlung
            buffer = b''
            start_time = time.time()
            timeout_s = receive_delay / 1000.0
            
            ''' print(f"[DEBUG] Erwarte {receive_len} Bytes...")'''
            
            while (time.time() - start_time) < timeout_s:
                if self.ser.in_waiting > 0:
                    # Lies verfügbare Bytes
                    chunk = self.ser.read(self.ser.in_waiting)
                    buffer += chunk
                    
                    # Debug-Ausgabe
                    ''' print(f"[DEBUG] Fragment erhalten: {chunk.hex(' ')} (Gesamt: {len(buffer)}/{receive_len})")'''
                    
                    # Wenn genug Daten da sind, sofort zurückgeben
                    if len(buffer) >= receive_len:
                        return buffer
                
                time.sleep(0.001)
            
            # Wenn Timeout erreicht
            ''' print(f"[DEBUG] Timeout! Erwartet: {receive_len}, Erhalten: {len(buffer)}") '''
            return None
            
        except Exception as e:
            print(f"Fehler in SendReceivePacket: {e}")
            return None
    

    ''' Hauptfunkion: zyklische Abfrage, z.B. 50 ms
            Lies Handad und aktualisiere Feed, Speed, Keeys,   State, LedState, KeyEventCount '''    
    def GetState(self):
        packet = bytes([0x24, 0x01, 0x02, 0xC8, 0x00, 0x00])
        data = self.SendReceivePacket(packet, 19, 0, 50)
        
        if data and len(data) >= 19:
            f = struct.unpack('<IHHHHB', data[5:18])
            
             # --- DEBUG-AUSGABE ---
            # key_event_count = f[5]
            ''' print(f"[DEBUG] GetState - KeyEventCount: {key_event_count} (Raw data bytes: {data.hex(' ')})")         '''   
            with self.lock:
                self.mAHWParams.update({
                    'Keys': f[0], 'Feed': f[1], 'Speed': f[2],
                    'State': f[3], 'LedState': f[4], 'KeyEventCount': f[5]
                })
            return 0
        return -1


    ''' Hauptfunkion: zyklische Abfrage, z.B. 50 ms
        Lies Tastatur-Eventbuffer, decodiere Ereignisse und löse Tastenevent aus.
        Anmerkung: Die Funktion GetKeyBuf() wird in der Schleife aufgerufen, bis der Puffer leer ist. 
        Prinzipiell kan mitn Hilfe von GetState der Status aller Tasten abgefragt werden. Die Auswertung
        des Ereignispuffers hat allerdings den Vorteil, dass kurz Tastenimpulse z.B. für den Tippbetrieb
        genau erfasst weden können.
        '''    
        
    def GetKeyBuf(self):
        MAX_KEYEVENT_RD = 5
        EV_AHW_KEYDOWN = 3
        EV_AHW_KEYUP = 2
        
        count = self.mAHWParams.get('KeyEventCount', 0)
        if count <= 0:
            return 0
            
        mReadCount = min(count, MAX_KEYEVENT_RD)
        out_data = bytes([mReadCount])
        
        # Sende-Länge für die Nutzlast (1 Byte Count + 5 Bytes pro Event)
        mReadLen = 1 + (6 * mReadCount)
        
        raw_response = self.SendCommand(self.CMD_GET_KEYBUF, out_data, mReadLen)
        
        # Das SendCommand Paket hat 6 Bytes Header+CRC. 
        # Nutzlast beginnt ab Index 6.
        PAYLOAD_START = 5
        
        # Validierung: Muss Payload-Start + 1 Byte (EventCount) enthalten
        if not raw_response or len(raw_response) < (PAYLOAD_START + 1):
            return -1

        # EventCount steht direkt nach dem Header (Index 6)
        event_count = raw_response[PAYLOAD_START]
        
        for i in range(event_count):
            # Die Events starten ab Index 7
            offset = PAYLOAD_START + 1 + (i * 5)
            
            if offset + 5 > len(raw_response):
                break
                
            keys, event_type = struct.unpack('<IB', raw_response[offset : offset + 5])
            
            if event_type in [EV_AHW_KEYDOWN, EV_AHW_KEYUP]:
                this_key = self.KeyCodeToKey(keys)
                
                if this_key >= 0:
                    key_obj = self.mHwdKeys[this_key]
                    
                    if event_type == EV_AHW_KEYDOWN:
                        now = int(time.time() * 1000)
                        key_obj.KeyDownTime = now
                        key_obj.KeyState = EV_AHW_KEYDOWN
                        key_obj.NextKeyEvent = now + key_obj.Intervall
                        key_obj.Changed = True
                        
                        if self.OnKeyDown:
                            self.OnKeyDown(self, this_key)
                    
                    self.mKeyChanged = True
                    
        return 0
 

    def connect(self):
        # self.SetMoveMode(self.MM_CONTINUOUS) # Initialzustand
        self.running = True        
        threading.Thread(target=self._loop, daemon=True).start()
        return True
   

    ''' Beispielloop zur Demonstartion zyklisches, abwechselndes Auslesen GetState und GetEventbuf '''
    def _loop(self):
        print("[INFO] Handrad-Thread gestartet...")
        time.sleep(1.0)
        self.ser.reset_input_buffer()
        mCurrentInfo = 0
        self.SetMoveMode(self.MM_CONTINUOUS)
       
        
        while self.running:
            mCurrentInfo += 1
            
            # 1. Hardware-Status abrufen (Stufenweise nach Delphi-Logik)
            if mCurrentInfo == 5:
                self.GetState()
            
            # 2. KeyBuf-Verarbeitung (Schleife zum kompletten Leeren des Puffers)
            if mCurrentInfo >= 10:
                ''' while self.mAHWParams.get('KeyEventCount', 0) > 0:
                     '''
                if self.GetKeyBuf() != 0:
                    break
                mCurrentInfo = 0
            
            # 3. Potentiometer-Überwachung (gedrosselt auf jeden 2. Durchlauf)
            if mCurrentInfo % 2 == 0:
                with self.lock:
                    f = self.mAHWParams.get('Feed', 0)
                    s = self.mAHWParams.get('Speed', 0)
                
                if abs(f - self.last_feed) > 10:
                    self.last_feed = f
                    if self.OnFeedChanged: self.OnFeedChanged(self, f)
                if abs(s - self.last_speed) > 10:
                    self.last_speed = s
                    if self.OnSpeedChanged: self.OnSpeedChanged(self, s)
            
            # 4. Cursor-Event-Verarbeitung
            self._process_cursor_keys()
            
            # 5. Zentrale Konsolenausgabe (nur zur Info)
            # ... (dein restlicher Code zur Ausgabe)
            
            time.sleep(0.01) # 10ms Takt
            
    ''' Verarbeitet die Cursor-Tasten und aktualisiert den Status '''        
    def _process_cursor_keys(self):
        now = int(time.time() * 1000)
        mNew = self.DecodeCursorKeys(self.mAHWParams.get('Keys', 0))
        mAnyChanged = False
        
        # Sicherstellen, dass wir den Puffer initialisieren, wenn er leer ist
        # Wir kombinieren den Puffer mit dem aktuellen Stand
        self.mCursorKeys |= mNew 
        
        mask_pairs = [(0x01, 0x02), (0x04, 0x08), (0x10, 0x20), (0x40, 0x80)]

        for i in range(4):
            MaskP, MaskN = mask_pairs[i]
            idxP, idxN = i * 2, i * 2 + 1
            
            # Verarbeite die Bits
            if (self.mCursorKeys & MaskP):
                self.mCursorKeys &= ~MaskP # Bit konsumieren
                mAnyChanged |= self._update_cursor_state(idxP, True, now)
            else:
                mAnyChanged |= self._update_cursor_state(idxP, False, now)

            if (self.mCursorKeys & MaskN):
                self.mCursorKeys &= ~MaskN # Bit konsumieren
                mAnyChanged |= self._update_cursor_state(idxN, True, now)
            else:
                mAnyChanged |= self._update_cursor_state(idxN, False, now)
            
        if mAnyChanged and self.OnCursorKeyChanged:
            self.OnCursorKeyChanged(self, self.mCursorKey)

    def _update_cursor_state(self, idx, is_pressed, now):
        """Der Zustandsautomat: LO -> POS_EDGE -> HI -> NEG_EDGE."""
        key = self.mCursorKey[idx]
        state = key.TriggerState
        changed = False
        
        if is_pressed:
            if state == SIGNAL_LO:
                key.TriggerState = SIGNAL_POS_EDGE
                key.NextKeyEvent = now + key.Intervall
                key.Changed = True
                changed = True
            elif state in [SIGNAL_POS_EDGE, SIGNAL_HI]:
                if now >= key.NextKeyEvent:
                    key.TriggerState = SIGNAL_HI
                    key.NextKeyEvent = now + key.Intervall
                    key.Changed = True
                    changed = True
        else:
            if state in [SIGNAL_POS_EDGE, SIGNAL_HI]:
                key.TriggerState = SIGNAL_NEG_EDGE
                key.Changed = True
                changed = True
            elif state == SIGNAL_NEG_EDGE:
                key.TriggerState = SIGNAL_LO
                key.Changed = False
                changed = True
                
        return changed
        
    def KeyCodeToKey(self, value):
        # Entspricht KeyMaskW3 aus deinem Delphi-Code
        key_masks = {
            1: 0x00000000, 2: 0x00000010, 3: 0x00000040, 4: 0x00000080,
            5: 0x00001000, 6: 0x00000008, 7: 0x00800000, 8: 0x00000001,
            9: 0x00100000, 10: 0x00040000, 11: 0x00000800, 12: 0x00000002,
            13: 0x04000000, 14: 0x02000000, 15: 0x00000100, 16: 0x01000000,
            17: 0x08000000, 18: 0x00000400, 19: 0x00004000, 20: 0x80000000,
            21: 0x00020000, 22: 0x00008000, 23: 0x00000004
        }
        for key_idx, mask in key_masks.items():
            if mask != 0 and (mask & value) != 0:
                return key_idx
        return -1
        
   
    def DecodeCursorKeys(self, Keys):
        """
        Übersetzt die rohen Hardware-Keys in Cursor-Bitmasken.
        Korrektur: Unabhängige If-Abfragen für Z- und A-Achse.
        """
        mNewCursorKeys = 0
        
        # X und Y Logik (bleibt wie gehabt)
        if self.KeyPressed(Keys, 2) != 0: mNewCursorKeys |= 0x0002 | 0x0004 # -x +y
        if self.KeyPressed(Keys, 8) != 0: mNewCursorKeys |= 0x0002 | 0x0008 # -x -y
        if self.KeyPressed(Keys, 4) != 0: mNewCursorKeys |= 0x0001 | 0x0004 # +x +y
        if self.KeyPressed(Keys, 10) != 0: mNewCursorKeys |= 0x0001 | 0x0008 # +x -y
        if self.KeyPressed(Keys, 6) != 0: mNewCursorKeys |= 0x0002         # -x
        if self.KeyPressed(Keys, 3) != 0: mNewCursorKeys |= 0x0004         # +y
        if self.KeyPressed(Keys, 7) != 0: mNewCursorKeys |= 0x0001         # +x
        if self.KeyPressed(Keys, 9) != 0: mNewCursorKeys |= 0x0008         # -y
        
        # Z-Achse: Eigenständige Abfragen (entferne das 'else')
        if self.KeyPressed(Keys, 5) != 0: mNewCursorKeys |= 0x0010         # Z+
        if self.KeyPressed(Keys, 11) != 0: mNewCursorKeys |= 0x0020        # Z-

        # A-Achse: Eigenständige Abfragen (entferne das 'else')
        if self.KeyPressed(Keys, 14) != 0: mNewCursorKeys |= 0x0040        # A+
        if self.KeyPressed(Keys, 12) != 0: mNewCursorKeys |= 0x0080        # A-

        # Widerspruchsprüfung: Wenn beide Richtungen gedrückt, lösche die Achse
        if (mNewCursorKeys & 0x0003) == 0x0003: mNewCursorKeys &= ~0x0003
        if (mNewCursorKeys & 0x000C) == 0x000C: mNewCursorKeys &= ~0x000C
        if (mNewCursorKeys & 0x0030) == 0x0030: mNewCursorKeys &= ~0x0030
        if (mNewCursorKeys & 0x00C0) == 0x00C0: mNewCursorKeys &= ~0x00C0

        return mNewCursorKeys
    
    def KeyPressed(self, KeyFlags, KeyNo):
        MIN_KEY, MAX_KEY = 1, 23
        if MIN_KEY <= KeyNo <= MAX_KEY:
            # Zugriff direkt über den Index
            if (KeyFlags & self.KeyMaskW3[KeyNo]) != 0:
                return 1
        return 0

    def calculate_crc(self, data):
        # Hier die Logik für dein CRC einfügen!
        # Falls es eine einfache XOR-Prüfsumme ist:
        crc = 0
        for byte in data:
            crc ^= byte
        #return crc
        return 0

    def SendCommand(self, cmd, out_data=None, expected_len=0):
        # 1. Basis-Paket erstellen
        out_data_len = len(out_data) if out_data else 0
        packet_len = out_data_len + 2
        header = struct.pack('<BBBBB', 0x24, 0x01, packet_len, cmd, out_data_len)
        packet = header + (bytes(out_data) if out_data else b'')
        
        # 2. CRC berechnen und anhängen
        crc = self.calculate_crc(packet)
        packet += bytes([crc]) # CRC an das Ende anhängen
        ''' print(f"[DEBUG] Sende Paket (Hex): {packet.hex(' ')}")
        print(f"[DEBUG] Erwarte Antwortlänge: {expected_len}")'''
        # 3. Senden (Achtung: Erwartete Länge erhöht sich jetzt um 1 Byte für das CRC!)
        total_receive_len = 6 + expected_len
        ''' print(f"[DEBUG] total receive len: {total_receive_len}") '''
        response = self.SendReceivePacket(packet, total_receive_len, 0, 3000)
        # DEBUG-PRINT für die Antwort
        '''
        if response:c
            print(f"[DEBUG] Empfangen: {response.hex(' ')}")
        else:
            print("[DEBUG] Empfangen: KEINE ANTWORT (Timeout)") '''
        return response

    def get_hardware_version(self):
        response = self.SendCommand(self.CMD_GET_HARDWARE_VER, None, expected_len=11)        
        if response:
            if isinstance(response, bytes):
                response = response.decode('ascii', errors='ignore')
            self.mHardwareVersion = response[4:].split('\x00')[0].strip()

    def get_compilation(self):
        response_comp = self.SendCommand(self.CMD_GET_COMPILATION, None, expected_len=40)      
        if response_comp:
            if isinstance(response_comp, bytes):
                response_comp = response_comp.decode('ascii', errors='ignore')                
            self.mCompilationDate = response_comp[4:].split('\x00')[0].strip()            

    def get_firmware_version(self):
        response_comp = self.SendCommand(self.CMD_GET_FIRMWARE_VER, None, expected_len=11)      
        if response_comp:
            if isinstance(response_comp, bytes):
                response_comp = response_comp.decode('ascii', errors='ignore')                
            self.mFirmwareVersion = response_comp[4:].split('\x00')[0].strip()                        

    def get_device_name(self):
        response_comp = self.SendCommand(self.CMD_GET_DEVICE_NAME, None, expected_len=40)      
        if response_comp:
            if isinstance(response_comp, bytes):
                response_comp = response_comp.decode('ascii', errors='ignore')                
            self.mDeviceName = response_comp[4:].split('\x00')[0].strip()                        
            
    def get_windows_com_ports(self):
              
        ports = serial.tools.list_ports.comports()
        
        # Extrahiert direkt nur den Port-Namen (z. B. 'COM9')
        self.mAvailableComPorts = [port.device for port in ports]
        
        return self.mAvailableComPorts
    
    def get_ahw_id(self):
        response_comp = self.SendCommand(self.CMD_GET_AHW_ID, None, expected_len=4)
        if response_comp:
            if isinstance(response_comp, bytes):
                id_bytes = response_comp[-5:-1]
                self.mAHWID = int.from_bytes(id_bytes, byteorder='little')
                return self.mAHWID
        return None
    
    def set_led(self, led_no: int, led_state: bool):
        """
        Steuert eine einzelne LED anhand ihrer Nummer und dem Zustand (True/False).
        Entspricht der Delphi-Funktion SetLed(LedNo: Byte; LedState: Boolean)
        """
        state_value = 1 if led_state else 0
        self.set_led_raw(led_no, state_value)

    def set_led_raw(self, led_no: int, led_state: int):
        """
        Sendet das rohe Byte-Paket fuer eine LED an die Hardware.
        Entspricht der Delphi-Funktion SetLed(LedNo: Byte; LedState: Byte)
        """
        # Packt LedNo und LedState als 2 einzelne Bytes (entspricht array[0..1] of Byte)
        out_data = bytes([led_no, led_state])
        
        # Sendet den Befehl 205 (CMD_SET_LED) mit den 2 Bytes Nutzdaten
        self.SendCommand(self.CMD_SET_LED, out_data, expected_len=0)

    @property
    def move_mode(self):
        """Eigenschaft zum Abfragen des aktuellen Modus."""
        return self._move_mode

    def SetMoveMode(self, new_mode: int):
        """
        Setzt den MoveMode (0-3) und steuert die zugehörigen LEDs.
        0: Continuous (alle LEDs aus)
        1: JOG1 (LED_JOG1 an)
        2: JOG2 (LED_JOG2 an)
        3: JOG3 (LED_JOG3 an)
        """
        self._move_mode = new_mode
        
        # Alle LEDs zunächst ausschalten
        self.set_led(self.LED_JOG1, False)
        self.set_led(self.LED_JOG2, False)
        self.set_led(self.LED_JOG3, False)
        
        # Passende LED für den neuen Modus aktivieren
        if self._move_mode == self.MM_JOG1:
            self.set_led(self.LED_JOG1, True)
        elif self._move_mode == self.MM_JOG2:
            self.set_led(self.LED_JOG2, True)
        elif self._move_mode == self.MM_JOG3:
            self.set_led(self.LED_JOG3, True)

    def IncMoveMode(self):
        """
        Rotiert den MoveMode durch: 0 -> 1 -> 2 -> 3 -> 0 ...
        """
        next_mode = (self._move_mode + 1) % 4
        self.SetMoveMode(next_mode)


    def disconnect(self):
        """Beendet den Thread und gibt die serielle Schnittstelle frei."""
        print("[INFO] Handrad-Treiber wird heruntergefahren...")
        
        # 1. Loop-Schleife stoppen
        self.running = False
        
        # 2. Seriellen Port sauber schliessen
        try:
            if hasattr(self, 'ser') and self.ser and self.ser.is_open:
                # Puffer leeren, falls noch Daten fliessen
                self.ser.reset_input_buffer()
                self.ser.reset_output_buffer()
                
                self.ser.close()
                print("[INFO] Serielle Schnittstelle erfolgreich freigegeben.")
        except Exception as e:
            print(f"Fehler beim Schliessen der Schnittstelle: {e}")
