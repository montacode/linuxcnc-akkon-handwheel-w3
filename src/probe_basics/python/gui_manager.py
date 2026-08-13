#!/usr/bin/env python3
import tkinter as tk
import os
import re

class InlineToolSelectorDialog:
    def __init__(self):
        self.is_open = False
        self.window = None
        self.listbox = None
        self.current_index = 0
        self.selected_tool_number = None
        self.tools_list = []
        
        self.tbl_path = os.path.expanduser("~/linuxcnc/configs/probe_basic/tool.tbl")

    def show(self):
        if self.is_open:
            if self.window:
                self.window.lift()
                self.window.focus_force()
            return

        self.window = tk.Toplevel()
        self.window.title("Werkzeug auswaehlen")
        
        # 1. Breite auf 550px erhoehen, damit der Text Platz hat
        self.window.geometry("550x450")
        self.window.attributes('-topmost', True)

        label = tk.Label(self.window, text="Werkzeug waehlen:", font=("Arial", 12, "bold"))
        label.pack(pady=10)

        # 2. Scrollbar erstellen und rechts platzieren
        scrollbar = tk.Scrollbar(self.window, orient=tk.VERTICAL)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        # 3. Listbox mit der Scrollbar verknuepfen
        self.listbox = tk.Listbox(
            self.window, 
            font=("Arial", 12), 
            selectmode=tk.SINGLE,
            exportselection=False,
            yscrollcommand=scrollbar.set
        )
        self.listbox.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        scrollbar.config(command=self.listbox.yview)

        self._load_tools()

        if self.tools_list:
            self.current_index = 0
            self._update_selection()

        self.window.lift()
        self.window.focus_force()
        self.is_open = True

    def _update_selection(self):
        if self.listbox and self.listbox.size() > 0:
            self.listbox.select_clear(0, tk.END)
            self.listbox.select_set(self.current_index)
            self.listbox.activate(self.current_index)
            self.listbox.see(self.current_index)
            
            if 0 <= self.current_index < len(self.tools_list):
                self.selected_tool_number = self.tools_list[self.current_index]

    def close(self):
        if self.window:
            try:
                self.window.destroy()
            except Exception:
                pass
            self.window = None
        self.is_open = False

    def move_up(self):
        if not self.is_open or not self.listbox or self.listbox.size() == 0:
            return
        if self.current_index > 0:
            self.current_index -= 1
            self._update_selection()

    def move_down(self):
        if not self.is_open or not self.listbox or self.listbox.size() == 0:
            return
        if self.current_index < self.listbox.size() - 1:
            self.current_index += 1
            self._update_selection()

    def get_selected_tool(self):
        """Liest das Werkzeug mit mehrfachen Fallbacks direkt aus."""
        # 1. Direkter Zugriff ueber aktuellen Index in der Werkzeugliste
        if self.tools_list and 0 <= self.current_index < len(self.tools_list):
            return self.tools_list[self.current_index]

        # 2. Zugriff ueber das gespeicherte Attribut
        if self.selected_tool_number is not None:
            return self.selected_tool_number

        # 3. Direkt aus dem Text der Listbox parsen (Fall: Notfall-Fallback)
        if self.listbox and self.listbox.size() > 0:
            try:
                text = self.listbox.get(self.current_index)
                match = re.search(r'T(\d+)', text)
                if match:
                    return int(match.group(1))
            except Exception:
                pass

        return None

    def _load_tools(self):
        self.tools_list = []
        if not os.path.exists(self.tbl_path):
            # Notfall-Werkzeuge (T1 - T9), falls tool.tbl fehlt
            for i in range(1, 10):
                self.listbox.insert(tk.END, f"T{i}: Werkzeug {i}")
                self.tools_list.append(i)
            if self.tools_list:
                self.selected_tool_number = self.tools_list[0]
            return

        try:
            with open(self.tbl_path, "r") as f:
                for line in f:
                    line = line.strip()
                    if line.startswith("T") and len(line) > 1 and line[1].isdigit():
                        match = re.search(r'T(\d+)', line)
                        if match:
                            tool_num = int(match.group(1))
                            comment = ""
                            if ";" in line:
                                comment = line.split(";", 1)[1].strip()
                            else:
                                comment = f"Werkzeug {tool_num}"
                            
                            self.listbox.insert(tk.END, f"T{tool_num}: {comment}")
                            self.tools_list.append(tool_num)

            if self.tools_list:
                self.selected_tool_number = self.tools_list[0]

        except Exception as e:
            print(f"[FEHLER] Lesen der tool.tbl fehlgeschlagen: {e}")
            for i in range(1, 10):
                self.listbox.insert(tk.END, f"T{i}: Werkzeug {i}")
                self.tools_list.append(i)
            if self.tools_list:
                self.selected_tool_number = self.tools_list[0]


class GUIManager:
    def __init__(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.tool_dialog = InlineToolSelectorDialog()

    def mainloop(self):
        self.root.mainloop()

    @property
    def is_dialog_open(self):
        return self.tool_dialog.is_open

    def zeige_werkzeug_dialog(self):
        def _toggle():
            if not self.tool_dialog.is_open:
                print("[GUI] Oeffne Werkzeugdialog...")
                self.tool_dialog.show()
            else:
                print("[GUI] Schliesse Werkzeugdialog...")
                self.tool_dialog.close()
        self.root.after(0, _toggle)

    def close_dialog(self):
        def _close():
            if self.tool_dialog.is_open:
                self.tool_dialog.close()
        self.root.after(0, _close)

    def move_up(self):
        self.root.after(0, self.tool_dialog.move_up)

    def move_down(self):
        self.root.after(0, self.tool_dialog.move_down)

    def get_selected_tool_async(self, callback):
        """Liest das Werkzeug aus, schliesst das Fenster IMMER und ruft den Callback auf."""
        def _fetch():
            tool_num = self.tool_dialog.get_selected_tool()
            
            # Fenster garantiert schliessen
            if self.tool_dialog.is_open:
                self.tool_dialog.close()

            # Callback mit der ermittelten Werkzeugnummer ausfuehren
            callback(tool_num)

        self.root.after(0, _fetch)
