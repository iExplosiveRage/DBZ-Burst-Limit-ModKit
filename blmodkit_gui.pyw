"""Double-click to open the blmodkit window (runs without a console with pythonw)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    from blmodkit.gui import main
except ImportError as e:  # numpy / Pillow missing
    import tkinter as tk
    from tkinter import messagebox
    r = tk.Tk()
    r.withdraw()
    messagebox.showerror("blmodkit", "Missing package: %s\n\nOpen a terminal in the kit folder and run:\n"
                                     "pip install -r requirements.txt" % e)
    sys.exit(1)

main()
